########
# Copyright (c) 2013 GigaSpaces Technologies Ltd. All rights reserved
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#        http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
############

import messages

__author__ = 'ran'

# Standard
import argparse
import argcomplete
import imp
import sys
import os
import traceback
import yaml
import json
import urlparse
import urllib
import shutil
from copy import deepcopy
from contextlib import contextmanager
import logging
import logging.config
import config
import formatting
from fabric.api import env, local
from fabric.context_managers import settings
from platform import system
from distutils.spawn import find_executable
from subprocess import call

# Project
from cosmo_manager_rest_client.cosmo_manager_rest_client \
    import CosmoManagerRestClient
from cosmo_manager_rest_client.cosmo_manager_rest_client \
    import (CosmoManagerRestCallError,
            CosmoManagerRestCallTimeoutError,
            CosmoManagerRestCallHTTPError)
from dsl_parser.parser import parse_from_path, DSLParsingException
from cloudify_rest_client import CloudifyClient


output_level = logging.INFO
CLOUDIFY_WD_SETTINGS_FILE_NAME = '.cloudify'

CONFIG_FILE_NAME = 'cloudify-config.yaml'
DEFAULTS_CONFIG_FILE_NAME = 'cloudify-config.defaults.yaml'

AGENT_MIN_WORKERS = 2
AGENT_MAX_WORKERS = 5
AGENT_KEY_PATH = '~/.ssh/cloudify-agents-kp.pem'
REMOTE_EXECUTION_PORT = 22

# http://stackoverflow.com/questions/8144545/turning-off-logging-in-paramiko
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("requests.packages.urllib3.connectionpool").setLevel(
    logging.ERROR)


def init_logger():
    """
    initializes a logger to be used throughout the cli
    can be used by provider codes.

    :rtype: `tupel` with 2 loggers, one for users (writes to console and file),
     and the other for archiving (writes to file only).
    """
    if os.path.isfile(config.LOG_DIR):
        sys.exit('file {0} exists - cloudify log directory cannot be created '
                 'there. please remove the file and try again.'
                 .format(config.LOG_DIR))
    try:
        logfile = config.LOGGER['handlers']['file']['filename']
        d = os.path.dirname(logfile)
        if not os.path.exists(d):
            os.makedirs(d)
        logging.config.dictConfig(config.LOGGER)
        lgr = logging.getLogger('main')
        lgr.setLevel(logging.INFO)
        flgr = logging.getLogger('file')
        flgr.setLevel(logging.DEBUG)
        return (lgr, flgr)
    except ValueError:
        sys.exit('could not initialize logger.'
                 ' verify your logger config'
                 ' and permissions to write to {0}'
                 .format(logfile))

# initialize logger
lgr, flgr = init_logger()


def main():
    args = _parse_args(sys.argv[1:])
    args.handler(args)


def _parse_args(args):
    """
    Parses the arguments using the Python argparse library.
    Generates shell autocomplete using the argcomplete library.

    :param list args: arguments from cli
    :rtype: `python argument parser`
    """
    # main parser
    parser = argparse.ArgumentParser(
        description='Manages Cloudify in different Cloud Environments')

    subparsers = parser.add_subparsers()
    parser_status = subparsers.add_parser(
        'status',
        help='Show a management server\'s status'
    )
    parser_use = subparsers.add_parser(
        'use',
        help='Use/switch to the specified management server'
    )
    parser_init = subparsers.add_parser(
        'init',
        help='Initialize configuration files for a specific cloud provider'

    )
    parser_bootstrap = subparsers.add_parser(
        'bootstrap',
        help='Bootstrap Cloudify on the currently active provider'
    )
    parser_teardown = subparsers.add_parser(
        'teardown',
        help='Teardown Cloudify'
    )
    parser_blueprints = subparsers.add_parser(
        'blueprints',
        help='Manages Cloudify\'s Blueprints'
    )
    parser_deployments = subparsers.add_parser(
        'deployments',
        help='Manages and Executes Cloudify\'s Deployments'
    )
    parser_executions = subparsers.add_parser(
        'executions',
        help='Manages Cloudify Executions'
    )
    parser_workflows = subparsers.add_parser(
        'workflows',
        help='Manages Deployment Workflows'
    )
    parser_events = subparsers.add_parser(
        'events',
        help='Displays Events for different executions'
    )
    parser_dev = subparsers.add_parser(
        'dev'
    )
    parser_ssh = subparsers.add_parser(
        'ssh',
        help='SSH to management server'
    )

    # status subparser
    _add_management_ip_optional_argument_to_parser(parser_status)
    _set_handler_for_command(parser_status, _status)

    # use subparser
    parser_use.add_argument(
        'management_ip',
        metavar='MANAGEMENT_IP',
        type=str,
        help='The cloudify management server ip address'
    )
    parser_use.add_argument(
        '-a', '--alias',
        dest='alias',
        metavar='ALIAS',
        type=str,
        help='An alias for the management server'
    )
    _add_force_optional_argument_to_parser(
        parser_use,
        'A flag indicating authorization to overwrite the alias if it '
        'already exists'
    )
    _set_handler_for_command(parser_use, _use_management_server)

    # init subparser
    parser_init.add_argument(
        'provider',
        metavar='PROVIDER',
        type=str,
        help='Command for initializing configuration files for a'
             ' specific provider'
    )
    parser_init.add_argument(
        '-t', '--target-dir',
        dest='target_dir',
        metavar='TARGET_DIRECTORY',
        type=str,
        default=os.getcwd(),
        help='The target directory to be initialized for the given provider'
    )
    parser_init.add_argument(
        '-r', '--reset-config',
        dest='reset_config',
        action='store_true',
        help='A flag indicating overwriting existing configuration is allowed'
    )
    parser_init.add_argument(
        '--install',
        dest='install',
        metavar='PROVIDER_MODULE_URL',
        type=str,
        help='url to provider module'
    )
    parser_init.add_argument(
        '--creds',
        dest='creds',
        metavar='PROVIDER_CREDENTIALS',
        type=str,
        help='a comma separated list of key=value credentials'
    )
    _set_handler_for_command(parser_init, _init_cosmo)

    # bootstrap subparser
    parser_bootstrap.add_argument(
        '-c', '--config-file',
        dest='config_file_path',
        metavar='CONFIG_FILE',
        default=None,
        type=str,
        help='Path to a provider configuration file'
    )
    parser_bootstrap.add_argument(
        '--keep-up-on-failure',
        dest='keep_up',
        action='store_true',
        help='A flag indicating that even if bootstrap fails,'
        ' the instance will remain running'
    )
    parser_bootstrap.add_argument(
        '--dev-mode',
        dest='dev_mode',
        action='store_true',
        help='A flag indicating that bootstrap will be run in dev-mode,'
        ' allowing to choose specific branches to run with'
    )
    parser_bootstrap.add_argument(
        '--skip-validations',
        dest='skip_validations',
        action='store_true',
        help='A flag indicating that bootstrap will be run without,'
        ' validating resources prior to bootstrapping the manager'
    )
    parser_bootstrap.add_argument(
        '--validate-only',
        dest='validate_only',
        action='store_true',
        help='A flag indicating that validations will run without,'
        ' actually performing the bootstrap process.'
    )
    _set_handler_for_command(parser_bootstrap, _bootstrap_cosmo)

    # teardown subparser
    parser_teardown.add_argument(
        '-c', '--config-file',
        dest='config_file_path',
        metavar='CONFIG_FILE',
        default=None,
        type=str,
        help='Path to a provider configuration file'
    )
    parser_teardown.add_argument(
        '--ignore-deployments',
        dest='ignore_deployments',
        action='store_true',
        help='A flag indicating confirmation for teardown even if there '
             'exist active deployments'
    )
    parser_teardown.add_argument(
        '--ignore-validation',
        dest='ignore_validation',
        action='store_true',
        help='A flag indicating confirmation for teardown even if there '
             'are validation conflicts'
    )
    _add_force_optional_argument_to_parser(
        parser_teardown,
        'A flag indicating confirmation for the teardown request')
    _add_management_ip_optional_argument_to_parser(parser_teardown)
    _set_handler_for_command(parser_teardown, _teardown_cosmo)

    # blueprints subparser
    blueprints_subparsers = parser_blueprints.add_subparsers()

    parser_blueprints_upload = blueprints_subparsers.add_parser(
        'upload',
        help='command for uploading a blueprint to the management server'
    )
    parser_blueprints_download = blueprints_subparsers.add_parser(
        'download',
        help='command for downloading a blueprint from the management server'
    )
    parser_blueprints_list = blueprints_subparsers.add_parser(
        'list',
        help='command for listing all uploaded blueprints'
    )
    parser_blueprints_delete = blueprints_subparsers.add_parser(
        'delete',
        help='command for deleting an uploaded blueprint'
    )
    parser_blueprints_validate = blueprints_subparsers.add_parser(
        'validate',
        help='command for validating a blueprint'
    )
    parser_blueprints_validate.add_argument(
        'blueprint_file',
        metavar='BLUEPRINT_FILE',
        type=argparse.FileType(),
        help='Path to blueprint file to be validated'
    )
    _set_handler_for_command(parser_blueprints_validate, _validate_blueprint)

    parser_blueprints_upload.add_argument(
        'blueprint_path',
        metavar='BLUEPRINT_FILE',
        type=str,
        help="Path to the application's blueprint file"
    )
    parser_blueprints_upload.add_argument(
        '-b', '--blueprint-id',
        dest='blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        default=None,
        required=False,
        help="Set the id of the uploaded blueprint"
    )
    _add_management_ip_optional_argument_to_parser(parser_blueprints_upload)
    _set_handler_for_command(parser_blueprints_upload, _upload_blueprint)

    _add_management_ip_optional_argument_to_parser(parser_blueprints_list)
    _set_handler_for_command(parser_blueprints_list, _list_blueprints)

    _add_management_ip_optional_argument_to_parser(parser_blueprints_download)
    _set_handler_for_command(parser_blueprints_download, _download_blueprint)

    parser_blueprints_download.add_argument(
        '-b', '--blueprint-id',
        dest='blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        required=True,
        help="The id fo the blueprint to download"
    )
    parser_blueprints_download.add_argument(
        '-o', '--output',
        dest='output',
        metavar='OUTPUT',
        type=str,
        required=False,
        help="The output file path of the blueprint to be downloaded"
    )

    parser_blueprints_delete.add_argument(
        '-b', '--blueprint-id',
        dest='blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        required=True,
        help="The id of the blueprint meant for deletion"
    )
    _add_management_ip_optional_argument_to_parser(parser_blueprints_delete)
    _set_handler_for_command(parser_blueprints_delete, _delete_blueprint)

    # deployments subparser
    deployments_subparsers = parser_deployments.add_subparsers()
    parser_deployments_create = deployments_subparsers.add_parser(
        'create',
        help='command for creating a deployment of a blueprint'
    )
    parser_deployments_delete = deployments_subparsers.add_parser(
        'delete',
        help='command for deleting a deployment'
    )
    parser_deployments_execute = deployments_subparsers.add_parser(
        'execute',
        help='command for executing a deployment of a blueprint'
    )
    parser_deployments_list = deployments_subparsers.add_parser(
        'list',
        help='command for listing all deployments or all deployments'
             'of a blueprint'
    )
    parser_deployments_create.add_argument(
        '-b', '--blueprint-id',
        dest='blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        required=True,
        help="The id of the blueprint meant for deployment"
    )
    parser_deployments_create.add_argument(
        '-d', '--deployment-id',
        dest='deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        required=True,
        help="A unique id that will be assigned to the created deployment"
    )
    _add_management_ip_optional_argument_to_parser(parser_deployments_create)
    _set_handler_for_command(parser_deployments_create, _create_deployment)

    parser_deployments_delete.add_argument(
        '-d', '--deployment-id',
        dest='deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        required=True,
        help="The deployment's id"
    )
    parser_deployments_delete.add_argument(
        '-f', '--ignore-live-nodes',
        dest='ignore_live_nodes',
        action='store_true',
        default=False,
        help='A flag indicating whether or not to delete the deployment even '
             'if there exist live nodes for it'
    )
    _add_management_ip_optional_argument_to_parser(parser_deployments_delete)
    _set_handler_for_command(parser_deployments_delete, _delete_deployment)

    parser_deployments_execute.add_argument(
        'operation',
        metavar='OPERATION',
        type=str,
        help='The operation to execute'
    )
    parser_deployments_execute.add_argument(
        '-d', '--deployment-id',
        dest='deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        required=True,
        help='The id of the deployment to execute the operation on'
    )
    parser_deployments_execute.add_argument(
        '--timeout',
        dest='timeout',
        metavar='TIMEOUT',
        type=int,
        required=False,
        default=900,
        help='Operation timeout in seconds (The execution itself will keep '
             'going, it is the CLI that will stop waiting for it to terminate)'
    )
    parser_deployments_execute.add_argument(
        '--force',
        dest='force',
        action='store_true',
        default=False,
        help='Whether the workflow should execute even if there is an ongoing'
             ' execution for the provided deployment'
    )
    _add_management_ip_optional_argument_to_parser(parser_deployments_execute)
    _add_include_logs_argument_to_parser(parser_deployments_execute)
    _set_handler_for_command(parser_deployments_execute,
                             _execute_deployment_operation)

    parser_deployments_list.add_argument(
        '-b', '--blueprint-id',
        dest='blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        required=False,
        help='The id of a blueprint to list deployments for'
    )
    _add_management_ip_optional_argument_to_parser(parser_deployments_list)
    _set_handler_for_command(parser_deployments_list,
                             _list_blueprint_deployments)

    # workflows subparser
    workflows_subparsers = parser_workflows.add_subparsers()
    parser_workflows_list = workflows_subparsers.add_parser(
        'list',
        help='command for listing workflows for a deployment')
    parser_workflows_list.add_argument(
        '-d', '--deployment-id',
        dest='deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        required=True,
        help='The id of the deployment whose workflows to list'
    )
    _add_management_ip_optional_argument_to_parser(parser_workflows_list)
    _set_handler_for_command(parser_workflows_list, _list_workflows)

    # Executions list sub parser
    executions_subparsers = parser_executions.add_subparsers()
    parser_executions_list = executions_subparsers.add_parser(
        'list',
        help='command for listing all executions of a deployment'
    )
    parser_executions_list.add_argument(
        '-d', '--deployment-id',
        dest='deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        required=True,
        help='The id of the deployment whose executions to list'
    )
    _add_management_ip_optional_argument_to_parser(parser_executions_list)
    _set_handler_for_command(parser_executions_list,
                             _list_deployment_executions)

    parser_executions_cancel = executions_subparsers.add_parser(
        'cancel',
        help='Cancel an execution by its id'
    )
    parser_executions_cancel.add_argument(
        '-e', '--execution-id',
        dest='execution_id',
        metavar='EXECUTION_ID',
        type=str,
        required=True,
        help='The id of the execution to cancel'
    )
    _add_management_ip_optional_argument_to_parser(parser_executions_cancel)
    _set_handler_for_command(parser_executions_cancel,
                             _cancel_execution)

    parser_events.add_argument(
        '-e', '--execution-id',
        dest='execution_id',
        metavar='EXECUTION_ID',
        type=str,
        required=True,
        help='The id of the execution to get events for'
    )
    _add_include_logs_argument_to_parser(parser_events)
    _add_management_ip_optional_argument_to_parser(parser_events)
    _set_handler_for_command(parser_events, _get_events)

    # dev subparser
    parser_dev.add_argument(
        'run',
        metavar='RUN',
        type=str,
        help='Command for running tasks.'
    )
    parser_dev.add_argument(
        '--tasks',
        dest='tasks',
        metavar='TASKS_LIST',
        type=str,
        help='A comma separated list of fabric tasks to run.'
    )
    parser_dev.add_argument(
        '--tasks-file',
        dest='tasks_file',
        metavar='TASKS_FILE',
        type=str,
        help='Path to a tasks file'
    )
    _add_management_ip_optional_argument_to_parser(parser_dev)
    _set_handler_for_command(parser_dev, _run_dev)

    # ssh subparser
    parser_ssh.add_argument(
        '-c', '--command',
        dest='ssh_command',
        metavar='COMMAND',
        default=None,
        type=str,
        help='Execute command over SSH'
    )
    parser_ssh.add_argument(
        '-p', '--plain',
        dest='ssh_plain_mode',
        action='store_true',
        help='Leave authentication to user'
    )
    _set_handler_for_command(parser_ssh, _run_ssh)

    argcomplete.autocomplete(parser)
    return parser.parse_args(args)


def _get_provider_module(provider_name, is_verbose_output=False):
    try:
        module_or_pkg_desc = imp.find_module(provider_name)
        if not module_or_pkg_desc[1]:
            # module_or_pkg_desc[1] is the pathname of found module/package,
            # if it's empty none were found
            msg = ('Provider {0} not found.'.format(provider_name))
            flgr.error(msg)
            raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)

        module = imp.load_module(provider_name, *module_or_pkg_desc)

        if not module_or_pkg_desc[0]:
            # module_or_pkg_desc[0] is None and module_or_pkg_desc[1] is not
            # empty only when we've loaded a package rather than a module.
            # Re-searching for the module inside the now-loaded package
            # with the same name.
            module = imp.load_module(
                provider_name,
                *imp.find_module(provider_name, module.__path__))
        return module
    except ImportError, ex:
        msg = ('Could not import module {0} '
               'maybe {0} provider module was not installed?'
               .format(provider_name))
        flgr.warning(msg)
        raise CosmoCliError(str(ex)) if is_verbose_output else sys.exit(msg)


def _add_include_logs_argument_to_parser(parser):
    parser.add_argument(
        '-l', '--include-logs',
        dest='include_logs',
        action='store_true',
        help='A flag whether to include logs in returned events'
    )


def _add_force_optional_argument_to_parser(parser, help_message):
    parser.add_argument(
        '-f', '--force',
        dest='force',
        action='store_true',
        help=help_message
    )


def _add_management_ip_optional_argument_to_parser(parser):
    parser.add_argument(
        '-t', '--management-ip',
        dest='management_ip',
        metavar='MANAGEMENT_IP',
        type=str,
        help='The cloudify management server ip address'
    )


def _set_handler_for_command(parser, handler):
    _add_verbosity_argument_to_parser(parser)

    def verbosity_aware_handler(args):
        global output_level
        if args.verbosity:
            lgr.setLevel(logging.DEBUG)
            output_level = logging.DEBUG
        handler(args)

    parser.set_defaults(handler=verbosity_aware_handler)


def _add_verbosity_argument_to_parser(parser):
    parser.add_argument(
        '-v', '--verbosity',
        dest='verbosity',
        action='store_true',
        help='A flag for setting verbose output'
    )


def set_global_verbosity_level(is_verbose_output=False):
    """
    sets the global verbosity level for console and the lgr logger.

    :param bool is_verbose_output: should be output be verbose
    :rtype: `None`
    """
    # we need both lgr.setLevel and the verbose_output parameter
    # since not all output is generated at the logger level.
    # verbose_output can help us control that.
    global verbose_output
    verbose_output = is_verbose_output
    if verbose_output:
        lgr.setLevel(logging.DEBUG)
    # print 'level is: ' + str(lgr.getEffectiveLevel())


def _read_config(config_file_path, provider_dir, is_verbose_output=False):

    def _deep_merge_dictionaries(overriding_dict, overridden_dict):
        merged_dict = deepcopy(overridden_dict)
        for k, v in overriding_dict.iteritems():
            if k in merged_dict and isinstance(v, dict):
                if isinstance(merged_dict[k], dict):
                    merged_dict[k] = \
                        _deep_merge_dictionaries(v, merged_dict[k])
                else:
                    raise RuntimeError('type conflict at key {0}'.format(k))
            else:
                merged_dict[k] = deepcopy(v)
        return merged_dict

    set_global_verbosity_level(is_verbose_output)
    if not config_file_path:
        config_file_path = CONFIG_FILE_NAME
    defaults_config_file_path = os.path.join(
        provider_dir,
        DEFAULTS_CONFIG_FILE_NAME)

    if not os.path.exists(config_file_path) or not os.path.exists(
            defaults_config_file_path):
        if not os.path.exists(defaults_config_file_path):
            raise ValueError('Missing the defaults configuration file; '
                             'expected to find it at {0}'.format(
                                 defaults_config_file_path))
        raise ValueError('Missing the configuration file; expected to find '
                         'it at {0}'.format(config_file_path))

    lgr.debug('reading provider config files')
    with open(config_file_path, 'r') as config_file, \
            open(defaults_config_file_path, 'r') as defaults_config_file:

        lgr.debug('safe loading user config')
        user_config = yaml.safe_load(config_file.read())

        lgr.debug('safe loading default config')
        defaults_config = yaml.safe_load(defaults_config_file.read())

    lgr.debug('merging configs')
    merged_config = _deep_merge_dictionaries(user_config, defaults_config) \
        if user_config else defaults_config
    return merged_config


def _init_cosmo(args):
    set_global_verbosity_level(args.verbosity)
    target_directory = os.path.expanduser(args.target_dir)
    provider = args.provider
    if not os.path.isdir(target_directory):
        msg = "Target directory doesn't exist."
        flgr.error(msg)
        raise CosmoCliError(msg) if args.verbosity else sys.exit(msg)

    if os.path.exists(os.path.join(target_directory,
                                   CLOUDIFY_WD_SETTINGS_FILE_NAME)):
        if not args.reset_config:
            msg = ('Target directory is already initialized. '
                   'Use the "-r" flag to force '
                   'reinitialization (might overwrite '
                   'provider configuration files if exist).')
            flgr.error(msg)
            raise CosmoCliError(msg) if args.verbosity else sys.exit(msg)

        else:  # resetting provider configuration
            lgr.debug('resetting configuration...')
            init(provider, target_directory,
                 args.reset_config,
                 creds=args.creds,
                 is_verbose_output=args.verbosity)
            lgr.info("Configuration reset complete")
            return

    lgr.info("Initializing Cloudify")
    provider_module_name = init(provider, target_directory,
                                args.reset_config,
                                args.install,
                                args.creds,
                                args.verbosity)
    # creating .cloudify file
    _dump_cosmo_working_dir_settings(CosmoWorkingDirectorySettings(),
                                     target_directory)
    with _update_wd_settings(args.verbosity) as wd_settings:
        wd_settings.set_provider(provider_module_name)
    lgr.info("Initialization complete")


def init(provider, target_directory, reset_config, install=False,
         creds=None, is_verbose_output=False):
        """
        iniatializes a provider by copying its config files to the cwd.
        First, will look for a module named cloudify_#provider#.
        If not found, will look for #provider#.
        If install is True, will install the supplied provider and perform
         the search again.

        :param string provider: the provider's name
        :param string target_directory: target directory for the config files
        :param bool reset_config: if True, overrides the current config.
        :param bool install: if supplied, will also install the desired
         provider according to the given url or module name (pypi).
        :param creds: a comma separated key=value list of credential info.
         this is specific to each provider.
        :param bool is_verbose_output: if True, output will be verbose.
        :rtype: `string` representing the provider's module name
        """
        set_global_verbosity_level(is_verbose_output)

        def _get_provider_by_name():
            try:
                # searching first for the standard name for providers
                # (i.e. cloudify_XXX)
                provider_module_name = 'cloudify_{0}'.format(provider)
                # print provider_module_name
                return (provider_module_name,
                        _get_provider_module(provider_module_name,
                                             is_verbose_output))
            except CosmoCliError:
                # if provider was not found, search for the exact literal the
                # user requested instead
                provider_module_name = provider
                return (provider_module_name,
                        _get_provider_module(provider_module_name,
                                             is_verbose_output))

        try:
            provider_module_name, provider = _get_provider_by_name()
        except:
            if install:
                local('pip install {0} --process-dependency-links'
                      .format(install))
            provider_module_name, provider = _get_provider_by_name()

        if not reset_config and os.path.exists(
                os.path.join(target_directory, CONFIG_FILE_NAME)):
            msg = ('Target directory already contains a '
                   'provider configuration file; '
                   'use the "-r" flag to '
                   'reset it back to its default values.')
            flgr.error(msg)
            raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)
        else:
            # try to get the path if the provider is a module
            try:
                provider_dir = provider.__path__[0]
            # if not, assume it's in the package's dir
            except:
                provider_dir = os.path.dirname(provider.__file__)
            files_path = os.path.join(provider_dir, CONFIG_FILE_NAME)
            lgr.debug('copying provider files from {0} to {1}'
                      .format(files_path, target_directory))
            shutil.copy(files_path, target_directory)

        if creds:
            src_config_file = '{}/{}'.format(provider_dir,
                                             DEFAULTS_CONFIG_FILE_NAME)
            dst_config_file = '{}/{}'.format(target_directory,
                                             CONFIG_FILE_NAME)
            with open(src_config_file, 'r') as f:
                provider_config = yaml.load(f.read())
                # print provider_config
                # TODO: handle cases in which creds might contain ',' or '='
                if 'credentials' in provider_config.keys():
                    for cred in creds.split(','):
                        key, value = cred.split('=')
                        if key in provider_config['credentials'].keys():
                            provider_config['credentials'][key] = value
                        else:
                            lgr.error('could not find key "{0}" in config file'
                                      .format(key))
                            raise CosmoCliError('key not found')
                else:
                    lgr.error('credentials section not found in config')
            # print yaml.dump(provider_config)
            with open(dst_config_file, 'w') as f:
                f.write(yaml.dump(provider_config, default_flow_style=False))

        return provider_module_name


def _bootstrap_cosmo(args):
    provider_name = _get_provider(args.verbosity)
    provider = _get_provider_module(provider_name, args.verbosity)
    try:
        provider_dir = provider.__path__[0]
    except:
        provider_dir = os.path.dirname(provider.__file__)
    provider_config = _read_config(args.config_file_path,
                                   provider_dir,
                                   args.verbosity)
    pm = provider.ProviderManager(provider_config, args.verbosity)

    if args.skip_validations and args.validate_only:
        sys.exit('please choose one of skip-validations or '
                 'validate-only flags, not both.')
    lgr.info("bootstrapping using {0}".format(provider_name))
    if not args.skip_validations:
        lgr.info('validating provider resources and configuration')
        validation_errors = {}
        if pm.schema is not None:
            validation_errors = pm.validate_schema(validation_errors,
                                                   schema=pm.schema)
        else:
            lgr.debug('schema validation disabled')
        # if the validation_errors dict return empty
        if not pm.validate(validation_errors) and not validation_errors:
            lgr.info('provider validations completed successfully')
        else:
            flgr.error('provider validations failed!')
            raise CosmoValidationError('provider validations failed!') \
                if args.verbosity else sys.exit('provider validations failed!')
    if args.validate_only:
        return
    with _protected_provider_call(args.verbosity):
        lgr.info('provisioning resources for management server...')
        params = pm.provision()

    provider_context = {}
    if params is not None:
        mgmt_ip, private_ip, ssh_key, ssh_user, provider_context = params
        lgr.info('provisioning complete')
        lgr.info('bootstrapping the management server...')
        installed = pm.bootstrap(mgmt_ip, private_ip, ssh_key,
                                 ssh_user, args.dev_mode)
        lgr.info('bootstrapping complete') if installed else \
            lgr.error('bootstrapping failed!')
    else:
        lgr.error('provisioning failed!')

    if params is not None and installed:
        _update_provider_context(provider_config, provider_context)

        mgmt_ip = mgmt_ip.encode('utf-8')

        with _update_wd_settings(args.verbosity) as wd_settings:
            wd_settings.set_management_server(mgmt_ip)
            wd_settings.set_management_key(ssh_key)
            wd_settings.set_management_user(ssh_user)
            wd_settings.set_provider_context(provider_context)

        # storing provider context on management server
        _get_rest_client(mgmt_ip).post_provider_context(provider_name,
                                                        provider_context)

        lgr.info(
            "management server is up at {0} (is now set as the default "
            "management server)".format(mgmt_ip))
    else:
        if args.keep_up:
            lgr.info('topology will remain up')
        else:
            lgr.info('tearing down topology'
                     ' due to bootstrap failure')
            pm.teardown(provider_context)
        raise CosmoBootstrapError() if args.verbosity else sys.exit(1)


def _update_provider_context(provider_config, provider_context):
    cloudify = provider_config.get('cloudify', {})
    agent = cloudify.get('cloudify_agent', {})
    min_workers = agent.get('min_workers', AGENT_MIN_WORKERS)
    max_workers = agent.get('max_workers', AGENT_MAX_WORKERS)
    user = agent.get('user')
    remote_execution_port = agent.get('remote_execution_port',
                                      REMOTE_EXECUTION_PORT)
    compute = provider_config.get('compute', {})
    agent_servers = compute.get('agent_servers', {})
    agents_keypair = agent_servers.get('agents_keypair', {})
    auto_generated = agents_keypair.get('auto_generated', {})
    private_key_target_path = auto_generated.get('private_key_target_path',
                                                 AGENT_KEY_PATH)
    provider_context['cloudify'] = {
        'cloudify_agent': {
            'min_workers': min_workers,
            'max_workers': max_workers,
            'agent_key_path': private_key_target_path,
            'remote_execution_port': remote_execution_port
        }
    }

    if user:
        provider_context['cloudify']['cloudify_agent']['user'] = user


def _teardown_cosmo(args):
    is_verbose_output = args.verbosity
    if not args.force:
        msg = ("This action requires additional "
               "confirmation. Add the '-f' or '--force' "
               "flags to your command if you are certain "
               "this command should be executed.")
        flgr.error(msg)
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)

    mgmt_ip = _get_management_server_ip(args)
    if not args.ignore_deployments and \
            len(_get_rest_client(mgmt_ip).list_deployments()) > 0:
        msg = ("Management server {0} has active deployments. Add the "
               "'--ignore-deployments' flag to your command to ignore "
               "these deployments and execute topology teardown."
               .format(mgmt_ip))
        flgr.error(msg)
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)

    provider_name, provider_context = \
        _get_provider_name_and_context(mgmt_ip, args.verbosity)
    provider = _get_provider_module(provider_name, args.verbosity)
    try:
        provider_dir = provider.__path__[0]
    except:
        provider_dir = os.path.dirname(provider.__file__)
    provider_config = _read_config(args.config_file_path,
                                   provider_dir,
                                   args.verbosity)
    pm = provider.ProviderManager(provider_config, args.verbosity)

    lgr.info("tearing down {0}".format(mgmt_ip))
    with _protected_provider_call(args.verbosity):
        pm.teardown(provider_context, args.ignore_validation)

    # cleaning relevant data from working directory settings
    with _update_wd_settings(args.verbosity) as wd_settings:
        # wd_settings.set_provider_context(provider_context)
        wd_settings.remove_management_server_context(mgmt_ip)

    lgr.info("teardown complete")


def _get_management_server_ip(args):
    is_verbose_output = args.verbosity
    cosmo_wd_settings = _load_cosmo_working_dir_settings(is_verbose_output)
    if hasattr(args, 'management_ip') and args.management_ip:
        return cosmo_wd_settings.translate_management_alias(
            args.management_ip)
    if cosmo_wd_settings.get_management_server():
        return cosmo_wd_settings.get_management_server()

    msg = ("Must either first run 'cfy use' command for a "
           "management server or provide a management "
           "server ip explicitly")
    flgr.error(msg)
    raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)


def _get_provider(is_verbose_output=False):
    cosmo_wd_settings = _load_cosmo_working_dir_settings(is_verbose_output)
    if cosmo_wd_settings.get_provider():
        return cosmo_wd_settings.get_provider()
    msg = "Provider is not set in working directory settings"
    flgr.error(msg)
    raise RuntimeError(msg) if is_verbose_output else sys.exit(msg)


def _get_mgmt_user(is_verbose_output=False):
    cosmo_wd_settings = _load_cosmo_working_dir_settings(is_verbose_output)
    if cosmo_wd_settings.get_management_user():
        return cosmo_wd_settings.get_management_user()
    msg = "Management User is not set in working directory settings"
    flgr.error(msg)
    raise RuntimeError(msg) if is_verbose_output else sys.exit(msg)


def _get_mgmt_key(is_verbose_output=False):
    cosmo_wd_settings = _load_cosmo_working_dir_settings(is_verbose_output)
    if cosmo_wd_settings.get_management_key():
        return cosmo_wd_settings.get_management_key()
    msg = "Management Key is not set in working directory settings"
    flgr.error(msg)
    raise RuntimeError(msg) if is_verbose_output else sys.exit(msg)


def _get_provider_name_and_context(mgmt_ip, is_verbose_output=False):
    # trying to retrieve provider context from server
    try:
        response = _get_rest_client(mgmt_ip).get_provider_context()
        return response['name'], response['context']
    except CosmoManagerRestCallError as e:
        lgr.warn('Failed to get provider context from server: {0}'.format(
            str(e)))

    # using the local provider context instead (if it's relevant for the
    # target server)
    cosmo_wd_settings = _load_cosmo_working_dir_settings(is_verbose_output)
    if cosmo_wd_settings.get_provider_context():
        default_mgmt_server_ip = cosmo_wd_settings.get_management_server()
        if default_mgmt_server_ip == mgmt_ip:
            provider_name = _get_provider(is_verbose_output)
            return provider_name, cosmo_wd_settings.get_provider_context()
        else:
            # the local provider context data is for a different server
            msg = "Failed to get provider context from target server"
    else:
        msg = "Provider context is not set in working directory settings (" \
              "The provider is used during the bootstrap and teardown " \
              "process. This probably means that the manager was started " \
              "manually, without the bootstrap command therefore calling " \
              "teardown is not supported)."
    flgr.error(msg)
    raise RuntimeError(msg) if is_verbose_output else sys.exit(msg)


def _status(args):
    management_ip = _get_management_server_ip(args)
    lgr.info(
        'querying management server {0}'.format(management_ip))

    status_result = _get_management_server_status(management_ip)
    if status_result:
        lgr.info(
            "REST service at management server {0} is up and running"
            .format(management_ip))

        lgr.info('Services information:')
        for service in status_result.services:
            lgr.info('\t{0}\t{1}'.format(
                service.display_name.ljust(20),
                service.instances[0]['state'] if service.instances else
                'Unknown'))
        return True
    else:
        lgr.info(
            "REST service at management server {0} is not responding"
            .format(management_ip))
        return False


def _get_management_server_status(management_ip):
    client = _get_rest_client(management_ip)
    try:
        return client.status()
    except CosmoManagerRestCallError:
        return None


def _use_management_server(args):
    if not os.path.exists(CLOUDIFY_WD_SETTINGS_FILE_NAME):
        # Allowing the user to work with an existing management server
        # even if "init" wasn't called prior to this.
        _dump_cosmo_working_dir_settings(CosmoWorkingDirectorySettings())

    if not _get_management_server_status(args.management_ip):
        msg = ("Can't use management server {0}: No response.".format(
            args.management_ip))
        flgr.error(msg)
        raise CosmoCliError(msg) if args.verbosity else sys.exit(msg)

    try:
        response = _get_rest_client(args.management_ip)\
            .get_provider_context()
        provider_name = response['name']
        provider_context = response['context']
    except CosmoManagerRestCallError:
        provider_name = None
        provider_context = None

    with _update_wd_settings(args.verbosity) as wd_settings:
        wd_settings.set_management_server(
            wd_settings.translate_management_alias(args.management_ip))
        wd_settings.set_provider_context(provider_context)
        wd_settings.set_provider(provider_name)
        if args.alias:
            wd_settings.save_management_alias(args.alias,
                                              args.management_ip,
                                              args.force,
                                              args.verbosity)
            lgr.info(
                'Using management server {0} (alias {1})'.format(
                    args.management_ip, args.alias))
        else:
            lgr.info('Using management server {0}'.format(
                     args.management_ip))


def _list_blueprints(args):
    management_ip = _get_management_server_ip(args)
    client = _get_new_rest_client(management_ip)

    lgr.info('Getting blueprints list... [manager={0}]'.format(management_ip))

    pt = formatting.table(['id', 'createdAt', 'updatedAt'],
                          data=client.blueprints.list())

    _output_table('Blueprints:', pt)


def _output_table(title, table):
    lgr.info('{0}{1}{0}{2}{0}'.format(os.linesep, title, table))


def _delete_blueprint(args):
    management_ip = _get_management_server_ip(args)
    blueprint_id = args.blueprint_id

    lgr.info(
        'Deleting blueprint {0} from management server {1}'.format(
            blueprint_id, management_ip))
    client = _get_rest_client(management_ip)
    client.delete_blueprint(blueprint_id)
    lgr.info("Deleted blueprint successfully")


def _delete_deployment(args):
    management_ip = _get_management_server_ip(args)
    deployment_id = args.deployment_id
    ignore_live_nodes = args.ignore_live_nodes

    lgr.info(
        'Deleting deployment {0} from management server {1}'.format(
            deployment_id, management_ip))
    client = _get_rest_client(management_ip)
    client.delete_deployment(deployment_id, ignore_live_nodes)
    lgr.info("Deleted deployment successfully")


def _upload_blueprint(args):
    is_verbose_output = args.verbosity
    blueprint_id = args.blueprint_id
    blueprint_path = os.path.expanduser(args.blueprint_path)
    if not os.path.isfile(blueprint_path):
        msg = ("Path to blueprint doesn't exist: {0}."
               .format(blueprint_path))
        flgr.error(msg)
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)

    management_ip = _get_management_server_ip(args)

    lgr.info(
        'Uploading blueprint {0} to management server {1}'.format(
            blueprint_path, management_ip))
    client = _get_rest_client(management_ip)
    blueprint_state = client.publish_blueprint(blueprint_path, blueprint_id)

    lgr.info(
        "Uploaded blueprint, blueprint's id is: {0}".format(
            blueprint_state.id))


def _create_deployment(args):
    blueprint_id = args.blueprint_id
    deployment_id = args.deployment_id
    management_ip = _get_management_server_ip(args)

    lgr.info('Creating new deployment from blueprint {0} at '
             'management server {1}'.format(blueprint_id, management_ip))
    client = _get_rest_client(management_ip)
    deployment = client.create_deployment(blueprint_id, deployment_id)
    lgr.info(
        "Deployment created, deployment's id is: {0}".format(
            deployment.id))


def _create_event_message_prefix(event):
    context = event['context']
    deployment_id = context['deployment_id']
    node_info = ''
    operation = ''
    if 'node_id' in context and context['node_id'] is not None:
        node_id = context['node_id']
        if 'operation' in context and context['operation'] is not None:
            operation = '.{0}'.format(context['operation'].split('.')[-1])
        node_info = '[{0}{1}] '.format(node_id, operation)
    level = 'CFY'
    message = event['message']['text'].encode('utf-8')
    if 'cloudify_log' in event['type']:
        level = 'LOG'
        message = '{0}: {1}'.format(event['level'].upper(), message)
    timestamp = event['@timestamp'].split('.')[0]

    return '{0} {1} <{2}> {3}{4}'.format(timestamp,
                                         level,
                                         deployment_id,
                                         node_info,
                                         message)


def _get_events_logger(args):
    def verbose_events_logger(events):
        for event in events:
            lgr.info(json.dumps(event, indent=4))

    def default_events_logger(events):
        for event in events:
            lgr.info(_create_event_message_prefix(event))

    if args.verbosity:
        return verbose_events_logger
    return default_events_logger


def _execute_deployment_operation(args):
    management_ip = _get_management_server_ip(args)
    operation = args.operation
    deployment_id = args.deployment_id
    timeout = args.timeout
    force = args.force
    include_logs = args.include_logs

    lgr.info("Executing workflow '{0}' on deployment '{1}' at"
             " management server {2} [timeout={3} seconds]"
             .format(operation, args.deployment_id, management_ip,
                     timeout))

    events_logger = _get_events_logger(args)
    client = _get_rest_client(management_ip)

    events_message = "* Run 'cfy events --include-logs "\
                     "--execution-id {0}' for retrieving the "\
                     "execution's events/logs"

    try:
        execution_id, error = client.execute_deployment(
            deployment_id,
            operation,
            events_logger,
            include_logs=include_logs,
            timeout=timeout,
            force=force)
        if error is None:
            lgr.info("Finished executing workflow '{0}' on deployment"
                     "'{1}'".format(operation, deployment_id))
            lgr.info(events_message.format(execution_id))
        else:
            lgr.info("Execution of workflow '{0}' for deployment "
                     "'{1}' failed. "
                     "[error={2}]".format(operation, deployment_id, error))
            lgr.info(events_message.format(execution_id))
            raise SuppressedCosmoCliError()
    except CosmoManagerRestCallTimeoutError, e:
        lgr.info("Execution of workflow '{0}' for deployment '{1}' timed out. "
                 "* Run 'cfy executions cancel --execution-id {2}' to cancel"
                 " the running workflow.".format(operation, deployment_id,
                                                 e.execution_id))
        lgr.info(events_message.format(e.execution_id))
        raise SuppressedCosmoCliError()


# TODO implement blueprint deployments on server side
# because it is currently filter by the CLI
def _list_blueprint_deployments(args):
    blueprint_id = args.blueprint_id
    management_ip = _get_management_server_ip(args)
    client = _get_new_rest_client(management_ip)
    if blueprint_id:
        lgr.info('Getting deployments list for blueprint: '
                 '\'{0}\'... [manager={1}]'.format(blueprint_id,
                                                   management_ip))
    else:
        lgr.info('Getting deployments list... '
                 '[manager={0}]'.format(management_ip))
    deployments = client.deployments.list()
    if blueprint_id:
        deployments = filter(lambda deployment:
                             deployment['blueprintId'] == blueprint_id,
                             deployments)

    pt = formatting.table(['id', 'blueprintId', 'createdAt', 'updatedAt'],
                          deployments)
    _output_table('Deployments:', pt)


def _list_workflows(args):
    management_ip = _get_management_server_ip(args)
    deployment_id = args.deployment_id
    client = _get_new_rest_client(management_ip)

    lgr.info('Getting workflows list for deployment: '
             '\'{0}\'... [manager={1}]'.format(deployment_id, management_ip))

    workflows = client.deployments.list_workflows(deployment_id)

    blueprint_id = workflows['blueprintId'] if \
        'blueprintId' in workflows else None
    deployment_id = workflows['deploymentId'] if \
        'deploymentId' in workflows else None

    pt = formatting.table(['blueprintId', 'deploymentId', 'name', 'createdAt'],
                          data=workflows.workflows,
                          defaults={'blueprintId': blueprint_id,
                                    'deploymentId': deployment_id})

    _output_table('Workflows:', pt)


def _cancel_execution(args):
    management_ip = _get_management_server_ip(args)
    client = _get_rest_client(management_ip)
    execution_id = args.execution_id
    lgr.info(
        'Canceling execution {0} on management server {1}'
        .format(execution_id, management_ip))
    client.cancel_execution(execution_id)
    lgr.info(
        'Cancelled execution {0} on management server {1}'
        .format(execution_id, management_ip))


def _list_deployment_executions(args):
    is_verbose_output = args.verbosity
    management_ip = _get_management_server_ip(args)
    client = _get_new_rest_client(management_ip)
    deployment_id = args.deployment_id
    try:
        lgr.info('Getting executions list for deployment: '
                 '\'{0}\' [manager={1}]'.format(deployment_id, management_ip))
        executions = client.executions.list(deployment_id)
    except CosmoManagerRestCallHTTPError, e:
        if not e.status_code == 404:
            raise
        msg = ('Deployment {0} does not exist on management server'
               .format(deployment_id))
        flgr.error(msg)
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)

    pt = formatting.table(['status', 'workflowId', 'deploymentId',
                           'blueprintId', 'error', 'id', 'createdAt'],
                          executions)
    _output_table('Executions:', pt)


def _get_events(args):
    management_ip = _get_management_server_ip(args)
    lgr.info("Getting events from management server {0} for "
             "execution id '{1}' "
             "[include_logs={2}]".format(management_ip,
                                         args.execution_id,
                                         args.include_logs))
    client = _get_rest_client(management_ip)
    try:
        events = client.get_all_execution_events(
            args.execution_id,
            include_logs=args.include_logs)
        events_logger = _get_events_logger(args)
        events_logger(events)
        lgr.info('\nTotal events: {0}'.format(len(events)))
    except CosmoManagerRestCallHTTPError, e:
        if e.status_code != 404:
            raise
        msg = ("Execution '{0}' not found on management server"
               .format(args.execution_id))
        flgr.error(msg)
        raise CosmoCliError(msg) if args.verbosity else sys.exit(msg)


def _run_dev(args):
    # TODO: allow passing username and key path as params.
    # env.user = args.user if args.user else _get_mgmt_user()
    # env.key_filename = args.key if args.key else _get_mgmt_key()
    env.user = _get_mgmt_user()
    env.key_filename = _get_mgmt_key()
    env.warn_only = True
    env.abort_on_prompts = False
    env.connection_attempts = 5
    env.keepalive = 0
    env.linewise = False
    env.pool_size = 0
    env.skip_bad_hosts = False
    env.timeout = 10
    env.forward_agent = True
    env.status = False
    env.disable_known_hosts = False

    mgmt_ip = args.management_ip if args.management_ip \
        else _get_management_server_ip(args)
    # hmm... it's also possible to just pass the tasks string to fabric
    # and let it run... need to think about it...
    if args.run:
        if args.tasks_file:
            sys.path.append(os.path.dirname(args.tasks_file))
            tasks = __import__(os.path.basename(os.path.splitext(
                args.tasks_file)[0]))
        else:
            sys.path.append(os.getcwd())
            try:
                import tasks
            except ImportError:
                raise CosmoDevError('could not find a tasks file to import.'
                                    ' either create a tasks.py file in your '
                                    'cwd or use the --tasks-file flag to '
                                    'point to one.')
        with settings(host_string=mgmt_ip):
            if args.tasks:
                for task in args.tasks.split(','):
                    try:
                        getattr(tasks, task)()
                    except AttributeError:
                        raise CosmoDevError('task: "{0}" not found'
                                            .format(task))
                    except Exception as e:
                        raise CosmoDevError('failed to execute: "{0}" '
                                            '({1}) '.format(task, str(e)))
            else:
                for task in dir(tasks):
                    if task.startswith('task_'):
                        try:
                            getattr(tasks, task)()
                        except Exception as e:
                            raise CosmoDevError('failed to execute: "{0}" '
                                                '({1}) '.format(task, str(e)))


def _run_ssh(args, is_verbose_output=False):
    ssh_path = find_executable('ssh')
    lgr.debug('SSH executable path: {0}'.format(ssh_path or 'Not found'))
    if not ssh_path and system() == 'Windows':
        msg = messages.SSH_WIN_NOT_FOUND
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)
    elif not ssh_path:
        msg = messages.SSH_LINUX_NOT_FOUND
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)
    else:
        _ssh(ssh_path, args)


def _ssh(path, args):
    command = [path]
    command.append('{0}@{1}'.format(_get_mgmt_user(),
                                    _get_management_server_ip(args)))
    if args.verbosity:
        command.append('-v')
    if not args.ssh_plain_mode:
        command.extend(['-i', _get_mgmt_key()])
    if args.ssh_command:
        command.extend(['--', args.ssh_command])
    lgr.debug('executing command: {0}'.format(' '.join(command)))
    lgr.info('Trying to connect...')
    call(command)


def _set_cli_except_hook():
    old_excepthook = sys.excepthook

    def new_excepthook(type, value, the_traceback):
        if type == CosmoCliError:
            lgr.error(str(value))
            if output_level <= logging.DEBUG:
                print("Stack trace:")
                traceback.print_tb(the_traceback)
        elif type == CosmoManagerRestCallError:
            lgr.error("Failed making a call to REST service: {0}".format(
                      str(value)))
            if output_level <= logging.DEBUG:
                print("Stack trace:")
                traceback.print_tb(the_traceback)
        elif type == SuppressedCosmoCliError:
            # output is already generated elsewhere
            # we only want and exit code that is not 0
            pass
        else:
            old_excepthook(type, value, the_traceback)

    sys.excepthook = new_excepthook


def _load_cosmo_working_dir_settings(is_verbose_output=False):
    try:
        with open('{0}'.format(CLOUDIFY_WD_SETTINGS_FILE_NAME), 'r') as f:
            return yaml.load(f.read())
    except IOError:
        msg = ('You must first initialize by running the '
               'command "cfy init", or choose to work with '
               'an existing management server by running the '
               'command "cfy use".')
        flgr.error(msg)
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)


def _dump_cosmo_working_dir_settings(cosmo_wd_settings, target_dir=None):
    target_file_path = '{0}'.format(CLOUDIFY_WD_SETTINGS_FILE_NAME) if \
        not target_dir else os.path.join(target_dir,
                                         CLOUDIFY_WD_SETTINGS_FILE_NAME)
    with open(target_file_path, 'w') as f:
        f.write(yaml.dump(cosmo_wd_settings))


def _download_blueprint(args):
    lgr.info(messages.DOWNLOADING_BLUEPRINT.format(args.blueprint_id))
    rest_client = _get_rest_client(_get_management_server_ip(args))
    target_file = rest_client.download_blueprint(args.blueprint_id,
                                                 args.output)
    lgr.info(messages.DOWNLOADING_BLUEPRINT_SUCCEEDED.format(
        args.blueprint_id,
        target_file))


def _validate_blueprint(args):
    is_verbose_output = args.verbosity
    target_file = args.blueprint_file

    resources = _get_resource_base()
    mapping = resources + "cloudify/alias-mappings.yaml"

    lgr.info(
        messages.VALIDATING_BLUEPRINT.format(target_file.name))
    try:
        parse_from_path(target_file.name, None, mapping, resources)
    except DSLParsingException as ex:
        msg = (messages.VALIDATING_BLUEPRINT_FAILED
               .format(target_file, str(ex)))
        flgr.error(msg)
        raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)
    lgr.info(messages.VALIDATING_BLUEPRINT_SUCCEEDED)


def _get_resource_base():
    script_directory = os.path.dirname(os.path.realpath(__file__))
    resource_directory = script_directory \
        + "/../../cloudify-manager/resources/rest-service/"
    if os.path.isdir(resource_directory):
        lgr.debug("Found resource directory")

        resource_directory_url = urlparse.urljoin('file:', urllib.pathname2url(
            resource_directory))
        return resource_directory_url
    lgr.debug("Using resources from github. Branch is develop")
    return "https://raw.githubusercontent.com/cloudify-cosmo/" \
           "cloudify-manager/develop/resources/rest-service/"


def _get_rest_client(management_ip):
    return CosmoManagerRestClient(management_ip)


def _get_new_rest_client(management_ip):
    return CloudifyClient(management_ip)


@contextmanager
def _update_wd_settings(is_verbose_output=False):
    cosmo_wd_settings = _load_cosmo_working_dir_settings(is_verbose_output)
    yield cosmo_wd_settings
    _dump_cosmo_working_dir_settings(cosmo_wd_settings)


@contextmanager
def _protected_provider_call(is_verbose_output=False):
    try:
        yield
    except Exception, ex:
        trace = sys.exc_info()[2]
        msg = ('Exception occurred in provider: {0}'
               .format(str(ex)))
        flgr.error(msg)
        raise CosmoCliError(msg), None, trace if is_verbose_output \
            else sys.exit(msg)


class CosmoWorkingDirectorySettings(yaml.YAMLObject):
    yaml_tag = u'!WD_Settings'
    yaml_loader = yaml.Loader

    def __init__(self):
        self._management_ip = None
        self._management_key = None
        self._management_user = None
        self._provider = None
        self._provider_context = None
        self._mgmt_aliases = {}
        self._mgmt_to_contextual_aliases = {}

    def get_management_server(self):
        return self._management_ip

    def set_management_server(self, management_ip):
        self._management_ip = management_ip

    def get_management_key(self):
        return self._management_key

    def set_management_key(self, management_key):
        self._management_key = management_key

    def get_management_user(self):
        return self._management_user

    def set_management_user(self, _management_user):
        self._management_user = _management_user

    def get_provider_context(self):
        return self._provider_context

    def set_provider_context(self, provider_context):
        self._provider_context = provider_context

    def remove_management_server_context(self, management_ip):
        # Clears management server context data.
        if management_ip in self._mgmt_to_contextual_aliases:
            del(self._mgmt_to_contextual_aliases[management_ip])

    def get_provider(self):
        return self._provider

    def set_provider(self, provider):
        self._provider = provider

    def translate_management_alias(self, management_address_or_alias):
        return self._mgmt_aliases[management_address_or_alias] if \
            management_address_or_alias in self._mgmt_aliases \
            else management_address_or_alias

    def save_management_alias(self, management_alias, management_address,
                              is_allow_overwrite, is_verbose_output=False):
        if not is_allow_overwrite and management_alias in self._mgmt_aliases:
            msg = ("management-server alias {0} is already in "
                   "use; use -f flag to allow overwrite."
                   .format(management_alias))
            flgr.error(msg)
            raise CosmoCliError(msg) if is_verbose_output else sys.exit(msg)
        self._mgmt_aliases[management_alias] = management_address


class CosmoDevError(Exception):
    pass


class CosmoBootstrapError(Exception):
    pass


class CosmoValidationError(Exception):
    pass


class CosmoCliError(Exception):
    pass


class SuppressedCosmoCliError(Exception):
    pass

if __name__ == '__main__':
    _set_cli_except_hook()  # only enable hook when this is called directly.
    main()
