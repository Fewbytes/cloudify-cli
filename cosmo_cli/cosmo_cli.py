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
#    * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
#    * See the License for the specific language governing permissions and
#    * limitations under the License.
import messages

__author__ = 'ran'

# Standard
import argparse
import imp
import sys
import os
import logging
import yaml
from copy import deepcopy
from contextlib import contextmanager


# Project
from cosmo_manager_rest_client.cosmo_manager_rest_client \
    import CosmoManagerRestClient
from cosmo_manager_rest_client.cosmo_manager_rest_client \
    import CosmoManagerRestCallError
from dsl_parser.parser import parse_from_path, DSLParsingException


CLOUDIFY_WD_SETTINGS_FILE_NAME = '.cloudify'
CONFIG_FILE_NAME = 'cloudify-config.yaml'
DEFAULTS_CONFIG_FILE_NAME = 'cloudify-config.defaults.yaml'

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# http://stackoverflow.com/questions/8144545/turning-off-logging-in-paramiko
logging.getLogger("paramiko").setLevel(logging.WARNING)
logging.getLogger("requests.packages.urllib3.connectionpool").setLevel(
    logging.WARNING)


def main():
    _set_cli_except_hook()
    args = _parse_args(sys.argv[1:])
    args.handler(args)


def other_method():
    print "baaa"

def parse_args(args):
    return _parse_args(args)

def _parse_args(args):
    #Parses the arguments using the Python argparse library

    #main parser
    parser = argparse.ArgumentParser(
        description='Installs Cosmo in an OpenStack environment')

    subparsers = parser.add_subparsers()
    parser_status = subparsers.add_parser(
        'status',
        help='Command for showing general status')
    parser_use = subparsers.add_parser(
        'use',
        help='Command for using a given management server')
    parser_init = subparsers.add_parser(
        'init',
        help='Command for initializing configuration files for installation')
    parser_bootstrap = subparsers.add_parser(
        'bootstrap',
        help='Command for bootstrapping cloudify')
    parser_teardown = subparsers.add_parser(
        'teardown',
        help='Command for tearing down cloudify')
    parser_blueprints = subparsers.add_parser(
        'blueprints',
        help='Commands for blueprints')
    parser_deployments = subparsers.add_parser(
        'deployments',
        help='Commands for deployments')
    parser_workflows = subparsers.add_parser(
        'workflows',
        help='Commands for workflows')

    parser_validate_blueprint = subparsers.add_parser(
        'validate',
        help='Validate blueprint format')


    #status subparser
    _add_management_ip_optional_argument_to_parser(parser_status)
    parser_status.set_defaults(handler=_status)

    #use subparser
    parser_use.add_argument(
        'management_ip',
        metavar='MANAGEMENT_IP',
        type=str,
        help='The cloudify management server ip address'
    )
    _add_alias_optional_argument_to_parser(parser_use, 'management server')
    _add_force_optional_argument_to_parser(
        parser_use,
        'A flag indicating authorization to overwrite the alias if it '
        'already exists')
    parser_use.set_defaults(handler=_use_management_server)

    #init subparser
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
    parser_init.set_defaults(handler=_init_cosmo)

    #bootstrap subparser
    parser_bootstrap.add_argument(
        '-c', '--config-file',
        dest='config_file',
        metavar='CONFIG_FILE',
        default=CONFIG_FILE_NAME,
        type=argparse.FileType(),
        help='Path to the cosmo configuration file'
    )
    parser_bootstrap.add_argument(
        '-d', '--defaults-config-file',
        dest='defaults_config_file',
        metavar='DEFAULTS_CONFIG_FILE',
        default=DEFAULTS_CONFIG_FILE_NAME,
        type=argparse.FileType(),
        help='Path to the cosmo defaults configuration file'
    )
    parser_bootstrap.add_argument(
        '-t', '--management-ip',
        dest='management_ip',
        metavar='MANAGEMENT_IP',
        type=str,
        help='Existing machine which should cosmo management should be '
             'installed and deployed on'
    )
    parser_bootstrap.set_defaults(handler=_bootstrap_cosmo)

    #teardown subparser
    _add_force_optional_argument_to_parser(
        parser_teardown,
        'A flag indicating confirmation for the teardown request')
    _add_management_ip_optional_argument_to_parser(parser_teardown)
    parser_teardown.set_defaults(handler=_teardown_cosmo)

    #blueprints subparser
    blueprints_subparsers = parser_blueprints.add_subparsers()

    _add_contextual_alias_subparser(blueprints_subparsers,
                                    'blueprint',
                                    _save_blueprint_alias_cmd)
    parser_blueprints_upload = blueprints_subparsers.add_parser(
        'upload',
        help='command for uploading a blueprint to the management server')
    parser_blueprints_list = blueprints_subparsers.add_parser(
        'list',
        help='command for listing all uploaded blueprints')
    parser_blueprints_delete = blueprints_subparsers.add_parser(
        'delete',
        help='command for deleting an uploaded blueprint')

    parser_blueprints_upload.add_argument(
        'blueprint_path',
        metavar='BLUEPRINT_FILE',
        type=str,
        help="Path to the application's blueprint file"
    )
    _add_alias_optional_argument_to_parser(parser_blueprints_upload,
                                           'blueprint')
    _add_management_ip_optional_argument_to_parser(parser_blueprints_upload)
    parser_blueprints_upload.set_defaults(handler=_upload_blueprint)

    _add_management_ip_optional_argument_to_parser(parser_blueprints_list)
    parser_blueprints_list.set_defaults(handler=_list_blueprints)

    parser_blueprints_delete.add_argument(
        'blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        help="The id or alias of the blueprint meant for deletion"
    )
    _add_management_ip_optional_argument_to_parser(parser_blueprints_delete)
    parser_blueprints_delete.set_defaults(handler=_delete_blueprint)

    #deployments subparser
    deployments_subparsers = parser_deployments.add_subparsers()
    _add_contextual_alias_subparser(deployments_subparsers,
                                    'deployment',
                                    _save_deployment_alias_cmd)
    parser_deployments_create = deployments_subparsers.add_parser(
        'create',
        help='command for creating a deployment of a blueprint')
    parser_deployments_execute = deployments_subparsers.add_parser(
        'execute',
        help='command for executing a deployment of a blueprint')

    parser_deployments_create.add_argument(
        'blueprint_id',
        metavar='BLUEPRINT_ID',
        type=str,
        help="The id or alias of the blueprint meant for deployment"
    )
    _add_alias_optional_argument_to_parser(parser_deployments_create,
                                           'deployment')
    _add_management_ip_optional_argument_to_parser(parser_deployments_create)
    parser_deployments_create.set_defaults(handler=_create_deployment)

    parser_deployments_execute.add_argument(
        'operation',
        metavar='OPERATION',
        type=str,
        help='The operation to execute'
    )
    parser_deployments_execute.add_argument(
        'deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        help='The id of the deployment to execute the operation on'
    )
    _add_management_ip_optional_argument_to_parser(parser_deployments_execute)
    parser_deployments_execute.set_defaults(
        handler=_execute_deployment_operation)

    #workflows subparser
    workflows_subparsers = parser_workflows.add_subparsers()
    parser_workflows_list = workflows_subparsers.add_parser(
        'list',
        help='command for listing workflows for a deployment')
    parser_workflows_list.add_argument(
        'deployment_id',
        metavar='DEPLOYMENT_ID',
        type=str,
        help='The id or alias of the deployment whose workflows to list'
    )
    _add_management_ip_optional_argument_to_parser(parser_workflows_list)
    parser_workflows_list.set_defaults(handler=_list_workflows)

    parser_validate_blueprint.add_argument(
        'blueprint_file',
        metavar='BLUEPRINT_FILE',
        type=str,
        help='Path to blueprint file to be validated'
    )
    parser_validate_blueprint.set_defaults(handler=_validate_blueprint)


    return parser.parse_args(args)


def _get_provider_module(provider_name):
    module_or_pkg_desc = imp.find_module(provider_name)
    if not module_or_pkg_desc[1]:
        #module_or_pkg_desc[1] is the pathname of found module/package,
        #if it's empty none were found
        raise CosmoCliError('Provider not found.')

    module = imp.load_module(provider_name, *module_or_pkg_desc)

    if not module_or_pkg_desc[0]:
        #module_or_pkg_desc[0] is None and module_or_pkg_desc[1] is not
        #empty only when we've loaded a package rather than a module.
        #Re-searching for the module inside the now-loaded package
        #with the same name.
        module = imp.load_module(
            provider_name,
            *imp.find_module(provider_name, module.__path__))
    return module


def _add_contextual_alias_subparser(subparsers_container, object_name,
                                    handler):
    alias_subparser = subparsers_container.add_parser(
        'alias',
        help='command for adding an alias for a {0}'.format(object_name))
    alias_subparser.add_argument(
        'alias',
        metavar='ALIAS',
        type=str,
        help='The alias for the {0}'.format(object_name)
    )
    alias_subparser.add_argument(
        '{0}_id'.format(object_name),
        metavar='{0}_ID'.format(object_name.upper()),
        type=str,
        help='The id of the {0}'.format(object_name)
    )
    _add_force_optional_argument_to_parser(
        alias_subparser,
        'A flag indicating authorization to overwrite the alias if '
        'it already exists')
    _add_management_ip_optional_argument_to_parser(alias_subparser)
    alias_subparser.set_defaults(handler=handler)


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


def _add_alias_optional_argument_to_parser(parser, object_name):
    parser.add_argument(
        '-a', '--alias',
        dest='alias',
        metavar='ALIAS',
        type=str,
        help='An alias for the {0}'.format(object_name)
    )


def _init_cosmo(args):
    logger.info("Initializing Cloudify")
    target_directory = args.target_dir
    #creating .cloudify file
    _dump_cosmo_working_dir_settings(CosmoWorkingDirectorySettings(),
                                     target_directory)

    try:
        #searching first for the standard name for providers
        #(i.e. cloudify_XXX)
        provider_module_name = 'cloudify_{0}'.format(args.provider)
        provider_module = _get_provider_module(provider_module_name)
    except CosmoCliError:
        #if provider was not found, search for the exact literal the
        #user requested instead
        provider_module_name = args.provider
        provider_module = _get_provider_module(provider_module_name)

    provider_module.init(logger, target_directory,
                         CONFIG_FILE_NAME, DEFAULTS_CONFIG_FILE_NAME)

    with _update_wd_settings() as wd_settings:
        wd_settings.set_provider(provider_module_name)
    logger.info("Initialization complete")


def _bootstrap_cosmo(args):
    provider = _get_provider()
    logger.info("Bootstrapping using {0}".format(provider))

    config = _read_config(args.config_file, args.defaults_config_file)
    mgmt_ip = _get_provider_module(provider).bootstrap(logger, config)
    mgmt_ip = mgmt_ip.encode('utf-8')

    with _update_wd_settings() as wd_settings:
        wd_settings.set_management_server(mgmt_ip)
    logger.info("Management server is up at {0} (is now set as the default "
                "management server)".format(mgmt_ip))


def _teardown_cosmo(args):
    if not args.force:
        raise CosmoCliError("This action requires additional confirmation. "
                            "Add the '-f' or '--force' flags to your command "
                            "if you are certain this command should"
                            " be executed.")

    mgmt_ip = _get_management_server_ip(args)
    logger.info("Tearing down {0}".format(mgmt_ip))

    provider = _get_provider()
    _get_provider_module(provider).teardown(logger, mgmt_ip)

    #cleaning relevant data from working directory settings
    with _update_wd_settings() as wd_settings:
        if wd_settings.remove_management_server_context(mgmt_ip):
            logger.info("No longer using management server {0} as the "
                        "default management server - run 'cfy use' "
                        "command to use a different server as default"
                        .format(mgmt_ip))

    logger.info("Teardown complete")


def _read_config(user_config_file, defaults_config_file):
    try:
        user_config = yaml.safe_load(user_config_file.read())
        defaults_config = yaml.safe_load(defaults_config_file.read())
    finally:
        user_config_file.close()
        defaults_config_file.close()

    merged_config = _deep_merge_dictionaries(user_config, defaults_config)
    return merged_config


def _deep_merge_dictionaries(overriding_dict, overridden_dict):
    merged_dict = deepcopy(overridden_dict)
    for k, v in overriding_dict.iteritems():
        if k in merged_dict and isinstance(v, dict):
            if isinstance(merged_dict[k], dict):
                merged_dict[k] = _deep_merge_dictionaries(v, merged_dict[k])
            else:
                raise RuntimeError('type conflict at key {0}'.format(k))
        else:
            merged_dict[k] = deepcopy(v)
    return merged_dict


def _translate_blueprint_alias(blueprint_id_or_alias, management_ip):
    wd_settings = _load_cosmo_working_dir_settings()
    return wd_settings.translate_blueprint_alias(blueprint_id_or_alias,
                                                 management_ip)


def _translate_deployment_alias(deployment_id_or_alias, management_ip):
    wd_settings = _load_cosmo_working_dir_settings()
    return wd_settings.translate_deployment_alias(deployment_id_or_alias,
                                                  management_ip)


def _save_blueprint_alias_cmd(args):
    mgmt_ip = _get_management_server_ip(args)
    is_allow_overwrite = True if args.force else False
    _save_blueprint_alias(args.alias, args.blueprint_id,
                          mgmt_ip, is_allow_overwrite)
    logger.info('Blueprint {0} is now aliased {1}'.format(
        args.blueprint_id, args.alias))


def _save_deployment_alias_cmd(args):
    mgmt_ip = _get_management_server_ip(args)
    is_allow_overwrite = True if args.force else False
    _save_deployment_alias(args.alias, args.deployment_id,
                           mgmt_ip, is_allow_overwrite)
    logger.info('Deployment {0} is now aliased {1}'.format(
        args.deployment_id, args.alias))


def _save_blueprint_alias(blueprint_alias, blueprint_id, management_ip,
                          is_allow_overwrite=False):
    with _update_wd_settings() as wd_settings:
        wd_settings.save_blueprint_alias(blueprint_alias, blueprint_id,
                                         management_ip, is_allow_overwrite)


def _save_deployment_alias(deployment_alias, deployment_id, management_ip,
                           is_allow_overwrite=False):
    with _update_wd_settings() as wd_settings:
        wd_settings.save_deployment_alias(deployment_alias, deployment_id,
                                          management_ip, is_allow_overwrite)


def _get_management_server_ip(args):
    cosmo_wd_settings = _load_cosmo_working_dir_settings()
    if args.management_ip:
        return cosmo_wd_settings.translate_management_alias(
            args.management_ip)
    if cosmo_wd_settings.get_management_server():
        return cosmo_wd_settings.get_management_server()
    raise CosmoCliError("Must either first run 'cfy use' command for a "
                        "management server or provide a management "
                        "server ip explicitly")


def _get_provider():
    cosmo_wd_settings = _load_cosmo_working_dir_settings()
    if cosmo_wd_settings.get_provider():
        return cosmo_wd_settings.get_provider()
    raise RuntimeError("Provider is not set in working directory settings")


def _get_blueprints_alias_mapping(management_ip):
    cosmo_wd_settings = _load_cosmo_working_dir_settings()
    return cosmo_wd_settings.get_blueprints_alias_mapping(management_ip)


def _status(args):
    management_ip = _get_management_server_ip(args)
    logger.info('querying management server {0}'.format(management_ip))
    client = CosmoManagerRestClient(management_ip)
    try:
        client.list_blueprints()
        logger.info("REST service at management server {0} is up and running"
                    .format(management_ip))
    except CosmoManagerRestCallError:
        logger.info("REST service at management server {0} is not responding"
                    .format(management_ip))


def _use_management_server(args):
    with _update_wd_settings() as wd_settings:
        wd_settings.set_management_server(
            wd_settings.translate_management_alias(args.management_ip))
        if args.alias:
            wd_settings.save_management_alias(args.alias,
                                              args.management_ip,
                                              args.force)
            logger.info('Using management server {0} (alias {1})'.format(
                args.management_ip, args.alias))
        else:
            logger.info('Using management server {0}'.format(
                args.management_ip))


def _list_blueprints(args):
    management_ip = _get_management_server_ip(args)
    logger.info('querying blueprints list from management '
                'server {0}'.format(management_ip))
    client = CosmoManagerRestClient(management_ip)
    blueprints_list = client.list_blueprints()
    alias_to_blueprint_id = _get_blueprints_alias_mapping(management_ip)
    blueprint_id_to_aliases = _build_reversed_lookup(alias_to_blueprint_id)

    if not blueprints_list:
        logger.info('There are no blueprints available on the '
                    'management server')
    else:
        logger.info('Blueprints:')
        for blueprint_state in blueprints_list:
            aliases_str = ''
            blueprint_id = blueprint_state.id
            if blueprint_id in blueprint_id_to_aliases:
                aliases_str = ''.join('{0}, '.format(alias) for alias in
                                      blueprint_id_to_aliases[blueprint_id])
                aliases_str = ' (' + aliases_str[:-2] + ')'
            logger.info('\t' + blueprint_id + aliases_str)

    #printing unused aliases if there are any
    blueprints_ids_on_server = {blueprint.id for blueprint in blueprints_list}
    unused_aliases = [alias for alias in alias_to_blueprint_id.iterkeys() if
                      alias_to_blueprint_id[alias] not in
                      blueprints_ids_on_server]
    if unused_aliases:
        logger.info('Unused aliases:')
        unused_aliases_str = '\t' + ''.join('{0}, '.format(alias)
                                            for alias in unused_aliases)
        logger.info(unused_aliases_str[:-2])


def _build_reversed_lookup(dic):
    rev_multidic = {}
    for k, v in dic.iteritems():
        if v not in rev_multidic:
            rev_multidic[v] = []
        rev_multidic[v].append(k)
    return rev_multidic


def _delete_blueprint(args):
    management_ip = _get_management_server_ip(args)
    blueprint_id = _translate_blueprint_alias(args.blueprint_id,
                                              management_ip)

    logger.info('Deleting blueprint {0} from management server {1}'.format(
        args.blueprint_id, management_ip))
    client = CosmoManagerRestClient(management_ip)
    client.delete_blueprint(blueprint_id)
    logger.info("Deleted blueprint successfully")


def _upload_blueprint(args):
    blueprint_path = args.blueprint_path
    management_ip = _get_management_server_ip(args)
    blueprint_alias = args.alias
    if blueprint_alias and \
            _translate_blueprint_alias(blueprint_alias,
                                       management_ip) != blueprint_alias:
        raise CosmoCliError('Blueprint alias {0} is already in use'.format(
            blueprint_alias))

    logger.info('Uploading blueprint {0} to management server {1}'.format(
        blueprint_path, management_ip))
    client = CosmoManagerRestClient(management_ip)
    blueprint_state = client.publish_blueprint(blueprint_path)

    if not blueprint_alias:
        logger.info("Uploaded blueprint, blueprint's id is: {0}".format(
            blueprint_state.id))
    else:
        _save_blueprint_alias(blueprint_alias,
                              blueprint_state.id,
                              management_ip)
        logger.info("Uploaded blueprint, blueprint's alias is: {0}"
                    " (id: {1})".format(blueprint_alias, blueprint_state.id))


def _create_deployment(args):
    blueprint_id = args.blueprint_id
    management_ip = _get_management_server_ip(args)
    translated_blueprint_id = _translate_blueprint_alias(blueprint_id,
                                                         management_ip)
    deployment_alias = args.alias
    if deployment_alias and \
            _translate_deployment_alias(deployment_alias,
                                        management_ip) != deployment_alias:
        raise CosmoCliError('Deployment alias {0} is already in use'.format(
            deployment_alias))

    logger.info('Creating new deployment from blueprint {0} at '
                'management server {1}'.format(blueprint_id, management_ip))
    client = CosmoManagerRestClient(management_ip)
    deployment = client.create_deployment(translated_blueprint_id)
    if not deployment_alias:
        logger.info("Deployment created, deployment's id is: {0}".format(
            deployment.id))
    else:
        _save_deployment_alias(deployment_alias, deployment.id, management_ip)
        logger.info("Deployment created, deployment's alias is: "
                    "{0} (id: {1})".format(deployment_alias, deployment.id))


def _execute_deployment_operation(args):
    management_ip = _get_management_server_ip(args)
    operation = args.operation
    deployment_id = _translate_deployment_alias(args.deployment_id,
                                                management_ip)

    logger.info('Executing operation {0} on deployment {1} at'
                ' management server {2}'
                .format(operation, args.deployment_id, management_ip))

    def events_logger(events):
        for event in events:
            logger.info(event)

    client = CosmoManagerRestClient(management_ip)
    client.execute_deployment(deployment_id, operation, events_logger)
    logger.info("Finished executing operation {0} on deployment".format(
        operation))


def _list_workflows(args):
    management_ip = _get_management_server_ip(args)
    deployment_id = _translate_deployment_alias(args.deployment_id,
                                                management_ip)

    logger.info('querying workflows list from management server {0} for '
                'deployment {1}'.format(management_ip, args.deployment_id))
    client = CosmoManagerRestClient(management_ip)
    workflow_names = [workflow.name for workflow in
                      client.list_workflows(deployment_id).workflows]
    logger.info("deployments workflows:")
    for name in workflow_names:
        logger.info("\t{0}".format(name))


def _set_cli_except_hook():
    old_excepthook = sys.excepthook

    def new_excepthook(type, value, the_traceback):
        if type == CosmoCliError:
            logger.error(value.message)
        elif type == CosmoManagerRestCallError:
            logger.error("Failed making a call to REST service: {0}".format(
                value.message))
        else:
            old_excepthook(type, value, the_traceback)

    sys.excepthook = new_excepthook


def _load_cosmo_working_dir_settings():
    try:
        with open('{0}'.format(CLOUDIFY_WD_SETTINGS_FILE_NAME), 'r') as f:
            return yaml.safe_load(f.read())
    except IOError:
        raise CosmoCliError('You must first initialize by running the '
                            'command "cfy init"')


def _dump_cosmo_working_dir_settings(cosmo_wd_settings, target_dir=None):
    target_file_path = '{0}'.format(CLOUDIFY_WD_SETTINGS_FILE_NAME) if \
        not target_dir else '{0}/{1}'.format(target_dir,
                                             CLOUDIFY_WD_SETTINGS_FILE_NAME)
    with open(target_file_path, 'w') as f:
        f.writenot(yaml.dump(cosmo_wd_settings))

def _validate_blueprint(args):
    target_file = args.blueprint_file

    if not os.path.isfile(target_file):
        raise CosmoCliError(messages.FILE_NOT_FOUND.format(target_file))

    # mapping = "file:/home/barakme/dev/cosmo/cosmo-manager/orchestrator/src/main/resources/org/cloudifysource/cosmo/dsl/alias-mappings.yaml"
    # resources = "file:/home/barakme/dev/cosmo/cosmo-manager/orchestrator/src/main/resources/"

    # mapping = "https://raw.github.com/CloudifySource/cosmo-manager/develop/orchestrator/src/main/resources/org/cloudifysource/cosmo/dsl/alias-mappings.yaml"
    # resources = "https://raw.github.com/CloudifySource/cosmo-manager/develop/orchestrator/src/main/resources/"
    resources = _getResourceBase()
    mapping = resources + "org/cloudifysource/cosmo/dsl/alias-mappings.yaml"

    logger.info(messages.VALIDATING_BLUEPRINT.format(target_file))
    try:
        parse_from_path(target_file, None, mapping, resources )
    except DSLParsingException as e:
        raise CosmoCliError(messages.VALIDATING_BLUEPRINT_FAILED.format(target_file, e.message))
    logger.info(messages.VALIDATING_BLUEPRINT_SUCCEEDED)

def _getResourceBase():
    script_directory = os.path.dirname(os.path.realpath(__file__))
    resource_directory = script_directory + "/../../cosmo-manager/orchestrator/src/main/resources/"
    if os.path.isdir(resource_directory):
        logger.debug("Found resource directory")
        import urlparse, urllib
        resource_directory_url = urlparse.urljoin('file:', urllib.pathname2url(resource_directory))
        return resource_directory_url
    logger.debug("Using resources from github")
    return "https://raw.github.com/CloudifySource/cosmo-manager/develop/orchestrator/src/main/resources/"


@contextmanager
def _update_wd_settings():
    cosmo_wd_settings = _load_cosmo_working_dir_settings()
    yield cosmo_wd_settings
    _dump_cosmo_working_dir_settings(cosmo_wd_settings)


class CosmoWorkingDirectorySettings(yaml.YAMLObject):
    yaml_tag = u'!WD_Settings'
    yaml_loader = yaml.SafeLoader

    def __init__(self):
        self._management_ip = None
        self._provider = None
        self._mgmt_aliases = {}
        self._mgmt_to_contextual_aliases = {}

    def get_management_server(self):
        return self._management_ip

    def set_management_server(self, management_ip):
        self._management_ip = management_ip

    def remove_management_server_context(self, management_ip):
        # Clears management server context data.
        # Returns True if the management server was the management
        #   server being used at the time of the call
        if management_ip in self._mgmt_to_contextual_aliases:
            del(self._mgmt_to_contextual_aliases[management_ip])
        if self._management_ip == management_ip:
            self._management_ip = None
            return True
        return False

    def get_provider(self):
        return self._provider

    def set_provider(self, provider):
        self._provider = provider

    def translate_management_alias(self, management_address_or_alias):
        return self._mgmt_aliases[management_address_or_alias] if \
            management_address_or_alias in self._mgmt_aliases \
            else management_address_or_alias

    def save_management_alias(self, management_alias, management_address,
                              is_allow_overwrite):
        if not is_allow_overwrite and management_alias in self._mgmt_aliases:
            raise CosmoCliError("management-server alias {1} is already in"
                                " use; use -f flag to allow overwrite."
                                .format(management_alias))
        self._mgmt_aliases[management_alias] = management_address

    def get_blueprints_alias_mapping(self, management_ip):
        if management_ip not in self._mgmt_to_contextual_aliases or \
           'blueprints' not in \
                self._mgmt_to_contextual_aliases[management_ip]:
            return {}
        return deepcopy(
            self._mgmt_to_contextual_aliases[management_ip]['blueprints'])

    def get_deployments_alias_mapping(self, management_ip):
        if management_ip not in self._mgmt_to_contextual_aliases or \
                'deployments' not in \
                self._mgmt_to_contextual_aliases[management_ip]:
            return {}
        return deepcopy(
            self._mgmt_to_contextual_aliases[management_ip]['deployments'])

    def translate_blueprint_alias(self, blueprint_id_or_alias,
                                  management_ip):
        return self._translate_contextual_alias('blueprints',
                                                blueprint_id_or_alias,
                                                management_ip)

    def translate_deployment_alias(self, deployment_id_or_alias,
                                   management_ip):
        return self._translate_contextual_alias('deployments',
                                                deployment_id_or_alias,
                                                management_ip)

    def save_blueprint_alias(self, blueprint_alias, blueprint_id,
                             management_ip, is_allow_overwrite):
        self._save_alias('blueprints', blueprint_alias, blueprint_id,
                         management_ip, is_allow_overwrite)

    def save_deployment_alias(self, deployment_alias, deployment_id,
                              management_ip, is_allow_overwrite):
        self._save_alias('deployments', deployment_alias, deployment_id,
                         management_ip, is_allow_overwrite)

    def _translate_contextual_alias(self, alias_type, id_or_alias,
                                    management_ip):
        if management_ip not in self._mgmt_to_contextual_aliases or \
                alias_type not in \
                self._mgmt_to_contextual_aliases[management_ip] or \
                id_or_alias not in \
                self._mgmt_to_contextual_aliases[management_ip][alias_type]:
            return id_or_alias

        contextual_aliases = self._mgmt_to_contextual_aliases[management_ip]
        return contextual_aliases[alias_type][id_or_alias]

    def _save_alias(self, alias_type, alias, id, management_ip,
                    is_allow_overwrite):
        if management_ip not in self._mgmt_to_contextual_aliases:
            self._mgmt_to_contextual_aliases[management_ip] = {}
            self._mgmt_to_contextual_aliases[management_ip][alias_type] = {}
        elif alias_type not in \
                self._mgmt_to_contextual_aliases[management_ip]:
            self._mgmt_to_contextual_aliases[management_ip][alias_type] = {}
        elif not is_allow_overwrite and alias in \
                self._mgmt_to_contextual_aliases[management_ip][alias_type]:
            raise CosmoCliError("{0} alias {1} is already in use; "
                                "use -f flag to allow overwrite."
                                .format(alias_type, alias))

        self._mgmt_to_contextual_aliases[management_ip][alias_type][alias] = id


class CosmoCliError(Exception):
    pass

if __name__ == '__main__':
    main()
