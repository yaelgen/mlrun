# Copyright 2018 Iguazio
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import collections.abc
import copy
import traceback
import typing
from http import HTTPStatus
from typing import Dict

import fastapi
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

import mlrun
import mlrun.api.api.deps
import mlrun.api.api.utils
import mlrun.api.crud
import mlrun.api.utils.auth.verifier
import mlrun.api.utils.clients.chief
import mlrun.api.utils.singletons.db
import mlrun.api.utils.singletons.project_member
import mlrun.common.schemas
import mlrun.projects.pipelines
from mlrun.api.api.utils import log_and_raise, parse_reference
from mlrun.utils.helpers import logger

router = fastapi.APIRouter()


@router.post(
    "/projects/{project}/workflows/{name}/submit",
    status_code=HTTPStatus.ACCEPTED.value,
    response_model=mlrun.common.schemas.WorkflowResponse,
)
async def submit_workflow(
    project: str,
    name: str,
    request: fastapi.Request,
    workflow_request: mlrun.common.schemas.WorkflowRequest = mlrun.common.schemas.WorkflowRequest(),
    auth_info: mlrun.common.schemas.AuthInfo = fastapi.Depends(
        mlrun.api.api.deps.authenticate_request
    ),
    db_session: Session = fastapi.Depends(mlrun.api.api.deps.get_db_session),
):
    """
    Submitting a workflow of existing project.
    To support workflow scheduling, we use here an auxiliary function called 'load_and_run'.
    This function runs remotely (in a distinct pod), loads a project and then runs the workflow.
    In this way we can run the workflow remotely with the workflow's engine or
    schedule this function which in every time loads the project and runs the workflow.
    Notice:
    in case of simply running a workflow, the returned run_id value is the id of the run of the auxiliary function.
    For getting the id and status of the workflow, use the `get_workflow_id` endpoint with the returned run id.

    :param project:             name of the project
    :param name:                name of the workflow
    :param request:             fastapi request for supporting rerouting to chief if needed
    :param workflow_request:    the request includes: workflow spec, arguments for the workflow, artifact path
                                as the artifact target path of the workflow, source url of the project for overriding
                                the existing one, run name to override the default: 'workflow-runner-<workflow name>'
                                and kubernetes namespace if other than default
    :param auth_info:           auth info of the request
    :param db_session:          session that manages the current dialog with the database

    :returns: response that contains the project name, workflow name, name of the workflow,
             status, run id (in case of a single run) and schedule (in case of scheduling)
    """
    project = await run_in_threadpool(
        mlrun.api.utils.singletons.project_member.get_project_member().get_project,
        db_session=db_session,
        name=project,
        leader_session=auth_info.session,
    )

    # check permission CREATE run
    await mlrun.api.utils.auth.verifier.AuthVerifier().query_project_resource_permissions(
        resource_type=mlrun.common.schemas.AuthorizationResourceTypes.run,
        project_name=project.metadata.name,
        resource_name=workflow_request.run_name or "",
        action=mlrun.common.schemas.AuthorizationAction.create,
        auth_info=auth_info,
    )
    # check permission READ workflow on project's workflow
    await mlrun.api.utils.auth.verifier.AuthVerifier().query_project_resource_permissions(
        resource_type=mlrun.common.schemas.AuthorizationResourceTypes.workflow,
        project_name=project.metadata.name,
        resource_name=name,
        action=mlrun.common.schemas.AuthorizationAction.read,
        auth_info=auth_info,
    )
    # Check permission CREATE workflow on new workflow's name
    await mlrun.api.utils.auth.verifier.AuthVerifier().query_project_resource_permissions(
        resource_type=mlrun.common.schemas.AuthorizationResourceTypes.workflow,
        project_name=project.metadata.name,
        # If workflow spec has not passed need to create on same name:
        resource_name=getattr(workflow_request.spec, "name", name),
        action=mlrun.common.schemas.AuthorizationAction.create,
        auth_info=auth_info,
    )
    # Re-route to chief in case of schedule
    if (
        _is_requested_schedule(name, workflow_request.spec, project)
        and mlrun.mlconf.httpdb.clusterization.role
        != mlrun.common.schemas.ClusterizationRole.chief
    ):
        chief_client = mlrun.api.utils.clients.chief.Client()
        return await chief_client.submit_workflow(
            project=project.metadata.name,
            name=name,
            request=request,
            json=workflow_request.dict(),
        )

    workflow_spec = _fill_workflow_missing_fields_from_project(
        project=project,
        workflow_name=name,
        spec=workflow_request.spec,
        arguments=workflow_request.arguments,
    )
    updated_request = workflow_request.copy()
    updated_request.spec = workflow_spec

    # This function is for loading the project and running workflow remotely.
    # In this way we can schedule workflows (by scheduling a job that runs the workflow)
    workflow_runner = await run_in_threadpool(
        mlrun.api.crud.WorkflowRunners().create_runner,
        run_name=updated_request.run_name
        or mlrun.mlconf.workflows.default_workflow_runner_name.format(
            workflow_spec.name
        ),
        project=project.metadata.name,
        db_session=db_session,
        auth_info=auth_info,
        image=workflow_spec.image
        or project.spec.default_image
        or mlrun.mlconf.default_base_image,
    )

    logger.debug(
        "Saved function for running workflow",
        project_name=workflow_runner.metadata.project,
        function_name=workflow_runner.metadata.name,
        workflow_name=workflow_spec.name,
        arguments=workflow_spec.args,
        source=updated_request.source or project.spec.source,
        kind=workflow_runner.kind,
        image=workflow_runner.spec.image,
    )

    run_uid = None
    status = None
    workflow_action = "schedule" if workflow_spec.schedule else "run"
    try:
        if workflow_spec.schedule:
            await run_in_threadpool(
                mlrun.api.crud.WorkflowRunners().schedule,
                runner=workflow_runner,
                project=project,
                workflow_request=updated_request,
                db_session=db_session,
                auth_info=auth_info,
            )
            status = "scheduled"

        else:
            run = await run_in_threadpool(
                mlrun.api.crud.WorkflowRunners().run,
                runner=workflow_runner,
                project=project,
                workflow_request=updated_request,
            )
            status = mlrun.run.RunStatuses.running
            run_uid = run.uid()
    except Exception as error:
        logger.error(traceback.format_exc())
        log_and_raise(
            reason="Workflow failed",
            workflow_name=workflow_spec.name,
            workflow_action=workflow_action,
            error=mlrun.errors.err_to_str(error),
        )

    return mlrun.common.schemas.WorkflowResponse(
        project=project.metadata.name,
        name=workflow_spec.name,
        status=status,
        run_id=run_uid,
        schedule=workflow_spec.schedule,
    )


def _is_requested_schedule(
    name: str,
    workflow_spec: mlrun.common.schemas.WorkflowSpec,
    project: mlrun.common.schemas.Project,
) -> bool:
    """
    Checks if the workflow needs to be scheduled, which can be decided either the request itself
    contains schedule information or the workflow which was predefined in the project contains schedule.

    :param name:            workflow name
    :param workflow_spec:   workflow spec input
    :param project:         MLRun project that contains the workflow

    :return: True if the workflow need to be scheduled and False if not.
    """
    if workflow_spec:
        return workflow_spec.schedule is not None

    project_workflow = _get_workflow_by_name(project, name)
    return bool(project_workflow.get("schedule"))


def _get_workflow_by_name(
    project: mlrun.common.schemas.Project, name: str
) -> typing.Optional[Dict]:
    """
    Getting workflow from project

    :param project:     MLRun project
    :param name:        workflow name

    :return: workflow as a dict if project has the workflow, otherwise raises a bad request exception
    """
    for workflow in project.spec.workflows:
        if workflow["name"] == name:
            return workflow
    log_and_raise(
        reason=f"workflow {name} not found in project",
    )


def _fill_workflow_missing_fields_from_project(
    project: mlrun.common.schemas.Project,
    workflow_name: str,
    spec: mlrun.common.schemas.WorkflowSpec,
    arguments: typing.Dict,
) -> mlrun.common.schemas.WorkflowSpec:
    """
    Fill the workflow spec details from the project object, with favour to spec

    :param project:         MLRun project that contains the workflow.
    :param workflow_name:   workflow name
    :param spec:            workflow spec input
    :param arguments:       arguments to workflow

    :return: completed workflow spec
    """
    # Verifying workflow exists in project:
    workflow = _get_workflow_by_name(project, workflow_name)

    if spec:
        # Merge between the workflow spec provided in the request with existing
        # workflow while the provided workflow takes precedence over the existing workflow params
        workflow = copy.deepcopy(workflow)
        workflow = _update_dict(workflow, spec.dict())

    workflow_spec = mlrun.common.schemas.WorkflowSpec(**workflow)
    # Overriding arguments of the existing workflow:
    if arguments:
        workflow_spec.args = workflow_spec.args or {}
        workflow_spec.args.update(arguments)

    return workflow_spec


def _update_dict(dict_1: dict, dict_2: dict):
    """
    Update two dictionaries included nested dictionaries (recursively).
    :param dict_1: The dict to update
    :param dict_2: The values of this dict take precedence over dict_1.
    :return:
    """
    for key, val in dict_2.items():
        if isinstance(val, collections.abc.Mapping):
            dict_1[key] = _update_dict(dict_1.get(key, {}), val)
        # It is necessary to update only if value is exist because
        # on initialization of the WorkflowSpec object all unfilled values gets None values,
        # and when converting to dict the keys gets those None values.
        elif val:
            dict_1[key] = val
    return dict_1


@router.get(
    "/projects/{project}/workflows/{name}/references/{reference}",
    response_model=mlrun.common.schemas.GetWorkflowResponse,
)
async def get_workflow_id(
    project: str,
    name: str,
    reference: str,
    auth_info: mlrun.common.schemas.AuthInfo = fastapi.Depends(
        mlrun.api.api.deps.authenticate_request
    ),
    db_session: Session = fastapi.Depends(mlrun.api.api.deps.get_db_session),
    engine: str = "kfp",
) -> mlrun.common.schemas.GetWorkflowResponse:
    """
    Retrieve workflow id from the uid of the workflow runner.
    When creating a remote workflow we are creating an auxiliary function
    which is responsible for actually running the workflow,
    as we don't know beforehand the workflow uid but only the run uid of the auxiliary function we ran,
    we have to wait until the running function will log the workflow id it created.
    Because we don't know how long it will take for the run to create the workflow
    we decided to implement that in an asynchronous mechanism which at first,
    client will get the run uid and then will pull the workflow id from the run id
    kinda as you would use a background task to query if it finished.
    Supporting workflows that executed by the remote engine **only**.

    :param project:     name of the project
    :param name:        name of the workflow
    :param reference:   the id of the running job that runs the workflow
    :param auth_info:   auth info of the request
    :param db_session:  session that manages the current dialog with the database
    :param engine:      pipeline runner, for example: "kfp"

    :returns: workflow id
    """
    _, uid = parse_reference(reference)
    # Check permission READ run:
    await mlrun.api.utils.auth.verifier.AuthVerifier().query_project_resource_permissions(
        mlrun.common.schemas.AuthorizationResourceTypes.run,
        project,
        uid,
        mlrun.common.schemas.AuthorizationAction.read,
        auth_info,
    )
    # Check permission READ workflow:
    await mlrun.api.utils.auth.verifier.AuthVerifier().query_project_resource_permissions(
        mlrun.common.schemas.AuthorizationResourceTypes.workflow,
        project,
        name,
        mlrun.common.schemas.AuthorizationAction.read,
        auth_info,
    )

    return await run_in_threadpool(
        mlrun.api.crud.WorkflowRunners().get_workflow_id,
        uid=uid,
        project=project,
        engine=engine,
        db_session=db_session,
    )
