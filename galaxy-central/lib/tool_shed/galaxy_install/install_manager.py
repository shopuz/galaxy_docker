import json
import logging
import os
import sys
import tempfile
import threading
import traceback

from galaxy import exceptions
from galaxy import eggs

eggs.require( 'paramiko' )
eggs.require( 'ssh' )
eggs.require( 'Fabric' )

from fabric.api import lcd

from galaxy.model.orm import or_

from tool_shed.util import basic_util
from tool_shed.util import common_util
from tool_shed.util import container_util
from tool_shed.util import data_manager_util
from tool_shed.util import datatype_util
from tool_shed.util import encoding_util
from tool_shed.util import hg_util
from tool_shed.util import metadata_util
from tool_shed.util import repository_dependency_util
from tool_shed.util import shed_util_common as suc
from tool_shed.util import tool_dependency_util
from tool_shed.util import tool_util
from tool_shed.util import xml_util

from tool_shed.galaxy_install.tool_dependencies.recipe.env_file_builder import EnvFileBuilder
from tool_shed.galaxy_install.tool_dependencies.recipe.install_environment import InstallEnvironment
from tool_shed.galaxy_install.tool_dependencies.recipe.recipe_manager import StepManager
from tool_shed.galaxy_install.tool_dependencies.recipe.recipe_manager import TagManager
from tool_shed.galaxy_install.repository_dependencies.repository_dependency_manager import RepositoryDependencyManager

log = logging.getLogger( __name__ )


class InstallToolDependencyManager( object ):
    
    def __init__( self, app ):
        self.app = app
        self.INSTALL_ACTIONS = [ 'download_binary', 'download_by_url', 'download_file', 'setup_perl_environment',
                                 'setup_r_environment', 'setup_ruby_environment', 'shell_command' ]

    def format_traceback( self ):
        ex_type, ex, tb = sys.exc_info()
        return ''.join( traceback.format_tb( tb ) )

    def get_tool_shed_repository_install_dir( self, tool_shed_repository ):
        return os.path.abspath( tool_shed_repository.repo_files_directory( self.app ) )

    def install_and_build_package( self, tool_shed_repository, tool_dependency, actions_dict ):
        """Install a Galaxy tool dependency package either via a url or a mercurial or git clone command."""
        tool_shed_repository_install_dir = self.get_tool_shed_repository_install_dir( tool_shed_repository )
        install_dir = actions_dict[ 'install_dir' ]
        package_name = actions_dict[ 'package_name' ]
        actions = actions_dict.get( 'actions', None )
        filtered_actions = []
        env_file_builder = EnvFileBuilder( install_dir )
        install_environment = InstallEnvironment( app=self.app,
                                                  tool_shed_repository_install_dir=tool_shed_repository_install_dir,
                                                  install_dir=install_dir )
        step_manager = StepManager( self.app )
        if actions:
            with install_environment.make_tmp_dir() as work_dir:
                with lcd( work_dir ):
                    # The first action in the list of actions will be the one that defines the initial download process.
                    # There are currently three supported actions; download_binary, download_by_url and clone via a
                    # shell_command action type.  The recipe steps will be filtered at this stage in the process, with
                    # the filtered actions being used in the next stage below.  The installation directory (i.e., dir)
                    # is also defined in this stage and is used in the next stage below when defining current_dir.
                    action_type, action_dict = actions[ 0 ]
                    if action_type in self.INSTALL_ACTIONS:
                        # Some of the parameters passed here are needed only by a subset of the step handler classes,
                        # but to allow for a standard method signature we'll pass them along.  We don't check the
                        # tool_dependency status in this stage because it should not have been changed based on a
                        # download.
                        tool_dependency, filtered_actions, dir = \
                            step_manager.execute_step( tool_dependency=tool_dependency,
                                                       package_name=package_name,
                                                       actions=actions,
                                                       action_type=action_type,
                                                       action_dict=action_dict,
                                                       filtered_actions=filtered_actions,
                                                       env_file_builder=env_file_builder,
                                                       install_environment=install_environment,
                                                       work_dir=work_dir,
                                                       current_dir=None,
                                                       initial_download=True )
                    else:
                        # We're handling a complex repository dependency where we only have a set_environment tag set.
                        # <action type="set_environment">
                        #    <environment_variable name="PATH" action="prepend_to">$INSTALL_DIR/bin</environment_variable>
                        # </action>
                        filtered_actions = [ a for a in actions ]
                        dir = install_dir
                    # We're in stage 2 of the installation process.  The package has been down-loaded, so we can
                    # now perform all of the actions defined for building it.
                    for action_tup in filtered_actions:
                        current_dir = os.path.abspath( os.path.join( work_dir, dir ) )
                        with lcd( current_dir ):
                            action_type, action_dict = action_tup
                            tool_dependency, tmp_filtered_actions, tmp_dir = \
                                step_manager.execute_step( tool_dependency=tool_dependency,
                                                           package_name=package_name,
                                                           actions=actions,
                                                           action_type=action_type,
                                                           action_dict=action_dict,
                                                           filtered_actions=filtered_actions,
                                                           env_file_builder=env_file_builder,
                                                           install_environment=install_environment,
                                                           work_dir=work_dir,
                                                           current_dir=current_dir,
                                                           initial_download=False )
                            if tool_dependency.status in [ self.app.install_model.ToolDependency.installation_status.ERROR ]:
                                # If the tool_dependency status is in an error state, return it with no additional
                                # processing.
                                return tool_dependency
                            # Make sure to handle the special case where the value of dir is reset (this happens when
                            # the action_type is change_directiory).  In all other action types, dir will be returned as
                            # None.
                            if tmp_dir is not None:
                                dir = tmp_dir
        return tool_dependency

    def install_and_build_package_via_fabric( self, tool_shed_repository, tool_dependency, actions_dict ):
        sa_session = self.app.install_model.context
        try:
            # There is currently only one fabric method.
            tool_dependency = self.install_and_build_package( tool_shed_repository, tool_dependency, actions_dict )
        except Exception, e:
            log.exception( 'Error installing tool dependency %s version %s.', str( tool_dependency.name ), str( tool_dependency.version ) )
            # Since there was an installation error, update the tool dependency status to Error. The remove_installation_path option must
            # be left False here.
            error_message = '%s\n%s' % ( self.format_traceback(), str( e ) )
            tool_dependency = tool_dependency_util.handle_tool_dependency_installation_error( self.app, 
                                                                                              tool_dependency, 
                                                                                              error_message, 
                                                                                              remove_installation_path=False )
        tool_dependency = self.mark_tool_dependency_installed( tool_dependency )
        return tool_dependency

    def install_specified_tool_dependencies( self, tool_shed_repository, tool_dependencies_config, tool_dependencies,
                                             from_tool_migration_manager=False ):
        """
        Follow the recipe in the received tool_dependencies_config to install specified packages for
        repository tools.  The received list of tool_dependencies are the database records for those
        dependencies defined in the tool_dependencies_config that are to be installed.  This list may
        be a subset of the set of dependencies defined in the tool_dependencies_config.  This allows
        for filtering out dependencies that have not been checked for installation on the 'Manage tool
        dependencies' page for an installed Tool Shed repository.
        """
        attr_tups_of_dependencies_for_install = [ ( td.name, td.version, td.type ) for td in tool_dependencies ]
        installed_packages = []
        tag_manager = TagManager( self.app )
        # Parse the tool_dependencies.xml config.
        tree, error_message = xml_util.parse_xml( tool_dependencies_config )
        if tree is None:
            log.debug( "The received tool_dependencies.xml file is likely invalid: %s" % str( error_message ) )
            return installed_packages
        root = tree.getroot()
        elems = []
        for elem in root:
            if elem.tag == 'set_environment':
                version = elem.get( 'version', '1.0' )
                if version != '1.0':
                    raise Exception( 'The <set_environment> tag must have a version attribute with value 1.0' )
                for sub_elem in elem:
                    elems.append( sub_elem )
            else:
                elems.append( elem )
        for elem in elems:
            name = elem.get( 'name', None )
            version = elem.get( 'version', None )
            type = elem.get( 'type', None )
            if type is None:
                if elem.tag in [ 'environment_variable', 'set_environment' ]:
                    type = 'set_environment'
                else:
                    type = 'package'
            if ( name and type == 'set_environment' ) or ( name and version ):
                # elem is a package set_environment tag set.
                attr_tup = ( name, version, type )
                try:
                    index = attr_tups_of_dependencies_for_install.index( attr_tup )
                except Exception, e:
                    index = None
                if index is not None:
                    tool_dependency = tool_dependencies[ index ]
                    # If the tool_dependency.type is 'set_environment', then the call to process_tag_set() will
                    # handle everything - no additional installation is necessary.
                    tool_dependency, proceed_with_install, action_elem_tuples = \
                        tag_manager.process_tag_set( tool_shed_repository,
                                                     tool_dependency,
                                                     elem,
                                                     name,
                                                     version,
                                                     from_tool_migration_manager=from_tool_migration_manager,
                                                     tool_dependency_db_records=tool_dependencies )
                    if ( tool_dependency.type == 'package' and proceed_with_install ):
                        try:
                            tool_dependency = self.install_package( elem, 
                                                                    tool_shed_repository, 
                                                                    tool_dependencies=tool_dependencies, 
                                                                    from_tool_migration_manager=from_tool_migration_manager )
                        except Exception, e:
                            error_message = "Error installing tool dependency %s version %s: %s" % \
                                ( str( name ), str( version ), str( e ) )
                            log.exception( error_message )
                            if tool_dependency:
                                # Since there was an installation error, update the tool dependency status to Error. The
                                # remove_installation_path option must be left False here.
                                tool_dependency = \
                                    tool_dependency_util.handle_tool_dependency_installation_error( self.app, 
                                                                                                    tool_dependency, 
                                                                                                    error_message, 
                                                                                                    remove_installation_path=False )
                        if tool_dependency and tool_dependency.status in [ self.app.install_model.ToolDependency.installation_status.INSTALLED,
                                                                           self.app.install_model.ToolDependency.installation_status.ERROR ]:
                            installed_packages.append( tool_dependency )
                            if self.app.config.manage_dependency_relationships:
                                # Add the tool_dependency to the in-memory dictionaries in the installed_repository_manager.
                                self.app.installed_repository_manager.handle_tool_dependency_install( tool_shed_repository, tool_dependency )
        return installed_packages

    def install_via_fabric( self, tool_shed_repository, tool_dependency, install_dir, package_name=None, custom_fabfile_path=None,
                            actions_elem=None, action_elem=None, **kwd ):
        """
        Parse a tool_dependency.xml file's <actions> tag set to gather information for installation using 
        self.install_and_build_package().  The use of fabric is being eliminated, so some of these functions
        may need to be renamed at some point.
        """
        sa_session = self.app.install_model.context
        if not os.path.exists( install_dir ):
            os.makedirs( install_dir )
        actions_dict = dict( install_dir=install_dir )
        if package_name:
            actions_dict[ 'package_name' ] = package_name
        actions = []
        is_binary_download = False
        if actions_elem is not None:
            elems = actions_elem
            if elems.get( 'os' ) is not None and elems.get( 'architecture' ) is not None:
                is_binary_download = True
        elif action_elem is not None:
            # We were provided with a single <action> element to perform certain actions after a platform-specific tarball was downloaded.
            elems = [ action_elem ]
        else:
            elems = []
        step_manager = StepManager( self.app )
        tool_shed_repository_install_dir = self.get_tool_shed_repository_install_dir( tool_shed_repository )
        install_environment = InstallEnvironment( self.app, tool_shed_repository_install_dir, install_dir )
        for action_elem in elems:
            # Make sure to skip all comments, since they are now included in the XML tree.
            if action_elem.tag != 'action':
                continue
            action_dict = {}
            action_type = action_elem.get( 'type', None )
            if action_type is not None:
                action_dict = step_manager.prepare_step( tool_dependency=tool_dependency,
                                                         action_type=action_type,
                                                         action_elem=action_elem,
                                                         action_dict=action_dict,
                                                         install_environment=install_environment,
                                                         is_binary_download=is_binary_download )
                action_tuple = ( action_type, action_dict )
                if action_type == 'set_environment':
                    if action_tuple not in actions:
                        actions.append( action_tuple )
                else:
                    actions.append( action_tuple )
        if actions:
            actions_dict[ 'actions' ] = actions
        if custom_fabfile_path is not None:
            # TODO: this is not yet supported or functional, but when it is handle it using the fabric api.
            raise Exception( 'Tool dependency installation using proprietary fabric scripts is not yet supported.' )
        else:
            tool_dependency = self.install_and_build_package_via_fabric( tool_shed_repository, tool_dependency, actions_dict )
        return tool_dependency

    def install_package( self, elem, tool_shed_repository, tool_dependencies=None, from_tool_migration_manager=False ):
        """
        Install a tool dependency package defined by the XML element elem.  The value of tool_dependencies is
        a partial or full list of ToolDependency records associated with the tool_shed_repository.
        """
        tag_manager = TagManager( self.app )
        # The value of package_name should match the value of the "package" type in the tool config's
        # <requirements> tag set, but it's not required.
        package_name = elem.get( 'name', None )
        package_version = elem.get( 'version', None )
        if tool_dependencies and package_name and package_version:
            tool_dependency = None
            for tool_dependency in tool_dependencies:
                if package_name == str( tool_dependency.name ) and package_version == str( tool_dependency.version ):
                    break
            if tool_dependency is not None:
                for package_elem in elem:
                    tool_dependency, proceed_with_install, actions_elem_tuples = \
                        tag_manager.process_tag_set( tool_shed_repository,
                                                     tool_dependency,
                                                     package_elem,
                                                     package_name,
                                                     package_version,
                                                     from_tool_migration_manager=from_tool_migration_manager,
                                                     tool_dependency_db_records=None )
                    if proceed_with_install and actions_elem_tuples:
                        # Get the installation directory for tool dependencies that will be installed for the received
                        # tool_shed_repository.
                        install_dir = \
                            tool_dependency_util.get_tool_dependency_install_dir( app=self.app,
                                                                                  repository_name=tool_shed_repository.name,
                                                                                  repository_owner=tool_shed_repository.owner,
                                                                                  repository_changeset_revision=tool_shed_repository.installed_changeset_revision,
                                                                                  tool_dependency_type='package',
                                                                                  tool_dependency_name=package_name,
                                                                                  tool_dependency_version=package_version )
                        # At this point we have a list of <actions> elems that are either defined within an <actions_group>
                        # tag set with <actions> sub-elements that contains os and architecture attributes filtered by the
                        # platform into which the appropriate compiled binary will be installed, or not defined within an
                        # <actions_group> tag set and not filtered.  Here is an example actions_elem_tuple.
                        # [(True, [<Element 'actions' at 0x109293d10>)]
                        binary_installed = False
                        for actions_elem_tuple in actions_elem_tuples:
                            in_actions_group, actions_elems = actions_elem_tuple
                            if in_actions_group:
                                # Platform matching is only performed inside <actions_group> tag sets, os and architecture
                                # attributes are otherwise ignored.
                                can_install_from_source = False
                                for actions_elem in actions_elems:
                                    system = actions_elem.get( 'os' )
                                    architecture = actions_elem.get( 'architecture' )
                                    # If this <actions> element has the os and architecture attributes defined, then we only
                                    # want to process until a successful installation is achieved.
                                    if system and architecture:
                                        # If an <actions> tag has been defined that matches our current platform, and the
                                        # recipe specified within that <actions> tag has been successfully processed, skip
                                        # any remaining platform-specific <actions> tags.  We cannot break out of the loop
                                        # here because there may be <action> tags at the end of the <actions_group> tag set
                                        # that must be processed.
                                        if binary_installed:
                                            continue
                                        # No platform-specific <actions> recipe has yet resulted in a successful installation.
                                        tool_dependency = self.install_via_fabric( tool_shed_repository,
                                                                                   tool_dependency,
                                                                                   install_dir,
                                                                                   package_name=package_name,
                                                                                   actions_elem=actions_elem,
                                                                                   action_elem=None )
                                        if tool_dependency.status == self.app.install_model.ToolDependency.installation_status.INSTALLED:
                                            # If an <actions> tag was found that matches the current platform, and
                                            # self.install_via_fabric() did not result in an error state, set binary_installed
                                            # to True in order to skip any remaining platform-specific <actions> tags.
                                            binary_installed = True
                                        else:
                                            # Process the next matching <actions> tag, or any defined <actions> tags that do not
                                            # contain platform dependent recipes.
                                            log.debug( 'Error downloading binary for tool dependency %s version %s: %s' % \
                                                ( str( package_name ), str( package_version ), str( tool_dependency.error_message ) ) )
                                    else:
                                        if actions_elem.tag == 'actions':
                                            # We've reached an <actions> tag that defines the recipe for installing and compiling from
                                            # source.  If binary installation failed, we proceed with the recipe.
                                            if not binary_installed:
                                                installation_directory = tool_dependency.installation_directory( self.app )
                                                if os.path.exists( installation_directory ):
                                                    # Delete contents of installation directory if attempt at binary installation failed.
                                                    installation_directory_contents = os.listdir( installation_directory )
                                                    if installation_directory_contents:
                                                        removed, error_message = tool_dependency_util.remove_tool_dependency( self.app,
                                                                                                                              tool_dependency )
                                                        if removed:
                                                            can_install_from_source = True
                                                        else:
                                                            log.debug( 'Error removing old files from installation directory %s: %s' % \
                                                                       ( str( installation_directory, str( error_message ) ) ) )
                                                    else:
                                                        can_install_from_source = True
                                                else:
                                                    can_install_from_source = True
                                            if can_install_from_source:
                                                # We now know that binary installation was not successful, so proceed with the <actions>
                                                # tag set that defines the recipe to install and compile from source.
                                                log.debug( 'Proceeding with install and compile recipe for tool dependency %s.' % \
                                                           str( tool_dependency.name ) )
                                                tool_dependency = self.install_via_fabric( tool_shed_repository,
                                                                                           tool_dependency,
                                                                                           install_dir,
                                                                                           package_name=package_name,
                                                                                           actions_elem=actions_elem,
                                                                                           action_elem=None )
                                    if actions_elem.tag == 'action' and \
                                        tool_dependency.status != self.app.install_model.ToolDependency.installation_status.ERROR:
                                        # If the tool dependency is not in an error state, perform any final actions that have been
                                        # defined within the actions_group tag set, but outside of an <actions> tag, which defines
                                        # the recipe for installing and compiling from source.
                                        tool_dependency = self.install_via_fabric( tool_shed_repository,
                                                                                   tool_dependency,
                                                                                   install_dir,
                                                                                   package_name=package_name,
                                                                                   actions_elem=None,
                                                                                   action_elem=actions_elem )
                            else:
                                # Checks for "os" and "architecture" attributes  are not made for any <actions> tag sets outside of
                                # an <actions_group> tag set.  If the attributes are defined, they will be ignored. All <actions> tags
                                # outside of an <actions_group> tag set will always be processed.
                                tool_dependency = self.install_via_fabric( tool_shed_repository,
                                                                           tool_dependency,
                                                                           install_dir,
                                                                           package_name=package_name,
                                                                           actions_elem=actions_elems,
                                                                           action_elem=None )
                                if tool_dependency.status != self.app.install_model.ToolDependency.installation_status.ERROR:
                                    log.debug( 'Tool dependency %s version %s has been installed in %s.' % \
                                        ( str( package_name ), str( package_version ), str( install_dir ) ) )
        return tool_dependency

    def mark_tool_dependency_installed( self, tool_dependency ):
        if tool_dependency.status not in [ self.app.install_model.ToolDependency.installation_status.ERROR,
                                           self.app.install_model.ToolDependency.installation_status.INSTALLED ]:
            log.debug( 'Changing status for tool dependency %s from %s to %s.' % \
                ( str( tool_dependency.name ),
                  str( tool_dependency.status ),
                  str( self.app.install_model.ToolDependency.installation_status.INSTALLED ) ) )
            status = self.app.install_model.ToolDependency.installation_status.INSTALLED
            tool_dependency = tool_dependency_util.set_tool_dependency_attributes( self.app,
                                                                                   tool_dependency=tool_dependency,
                                                                                   status=status,
                                                                                   error_message=None,
                                                                                   remove_from_disk=False )
        return tool_dependency


class InstallRepositoryManager( object ):

    def __init__( self, app ):
        self.app = app

    def get_repository_components_for_installation( self, encoded_tsr_id, encoded_tsr_ids, repo_info_dicts,
                                                    tool_panel_section_keys ):
        """
        The received encoded_tsr_ids, repo_info_dicts, and tool_panel_section_keys are 3 lists that
        contain associated elements at each location in the list.  This method will return the elements
        from repo_info_dicts and tool_panel_section_keys associated with the received encoded_tsr_id
        by determining its location in the received encoded_tsr_ids list.
        """
        for index, tsr_id in enumerate( encoded_tsr_ids ):
            if tsr_id == encoded_tsr_id:
                repo_info_dict = repo_info_dicts[ index ]
                tool_panel_section_key = tool_panel_section_keys[ index ]
                return repo_info_dict, tool_panel_section_key
        return None, None

    def __get_install_info_from_tool_shed( self, tool_shed_url, name, owner, changeset_revision ):
        params = '?name=%s&owner=%s&changeset_revision=%s' % ( name, owner, changeset_revision )
        url = common_util.url_join( tool_shed_url,
                                    'api/repositories/get_repository_revision_install_info%s' % params )
        try:
            raw_text = common_util.tool_shed_get( self.app, tool_shed_url, url )
        except Exception, e:
            message = "Error attempting to retrieve installation information from tool shed "
            message += "%s for revision %s of repository %s owned by %s: %s" % \
                ( str( tool_shed_url ), str( changeset_revision ), str( name ), str( owner ), str( e ) )
            log.warn( message )
            raise exceptions.InternalServerError( message )
        if raw_text:
            # If successful, the response from get_repository_revision_install_info will be 3
            # dictionaries, a dictionary defining the Repository, a dictionary defining the
            # Repository revision (RepositoryMetadata), and a dictionary including the additional
            # information required to install the repository.
            items = json.loads( raw_text )
            repository_revision_dict = items[ 1 ]
            repo_info_dict = items[ 2 ]
        else:
            message = "Unable to retrieve installation information from tool shed %s for revision %s of repository %s owned by %s: %s" % \
                ( str( tool_shed_url ), str( changeset_revision ), str( name ), str( owner ), str( e ) )
            log.warn( message )
            raise exceptions.InternalServerError( message )
        # Make sure the tool shed returned everything we need for installing the repository.
        if not repository_revision_dict or not repo_info_dict:
            invalid_parameter_message = "No information is available for the requested repository revision.\n"
            invalid_parameter_message += "One or more of the following parameter values is likely invalid:\n"
            invalid_parameter_message += "tool_shed_url: %s\n" % str( tool_shed_url )
            invalid_parameter_message += "name: %s\n" % str( name )
            invalid_parameter_message += "owner: %s\n" % str( owner )
            invalid_parameter_message += "changeset_revision: %s\n" % str( changeset_revision )
            raise exceptions.RequestParameterInvalidException( invalid_parameter_message )
        repo_info_dicts = [ repo_info_dict ]
        return repository_revision_dict, repo_info_dicts

    def handle_repository_contents( self, tool_shed_repository, tool_path, repository_clone_url, relative_install_dir,
                                    tool_shed=None, tool_section=None, shed_tool_conf=None, reinstalling=False ):
        """
        Generate the metadata for the installed tool shed repository, among other things.  This method is called from Galaxy
        (never the tool shed) when an administrator is installing a new repository or reinstalling an uninstalled repository.
        """
        install_model = self.app.install_model
        shed_config_dict = self.app.toolbox.get_shed_config_dict_by_filename( shed_tool_conf )
        metadata_dict, invalid_file_tups = \
            metadata_util.generate_metadata_for_changeset_revision( app=self.app,
                                                                    repository=tool_shed_repository,
                                                                    changeset_revision=tool_shed_repository.changeset_revision,
                                                                    repository_clone_url=repository_clone_url,
                                                                    shed_config_dict=shed_config_dict,
                                                                    relative_install_dir=relative_install_dir,
                                                                    repository_files_dir=None,
                                                                    resetting_all_metadata_on_repository=False,
                                                                    updating_installed_repository=False,
                                                                    persist=True )
        tool_shed_repository.metadata = metadata_dict
        # Update the tool_shed_repository.tool_shed_status column in the database.
        tool_shed_status_dict = suc.get_tool_shed_status_for_installed_repository( self.app, tool_shed_repository )
        if tool_shed_status_dict:
            tool_shed_repository.tool_shed_status = tool_shed_status_dict
        install_model.context.add( tool_shed_repository )
        install_model.context.flush()
        if 'tool_dependencies' in metadata_dict and not reinstalling:
            tool_dependencies = tool_dependency_util.create_tool_dependency_objects( self.app,
                                                                                     tool_shed_repository,
                                                                                     relative_install_dir,
                                                                                     set_status=True )
        if 'sample_files' in metadata_dict:
            sample_files = metadata_dict.get( 'sample_files', [] )
            tool_index_sample_files = tool_util.get_tool_index_sample_files( sample_files )
            tool_data_table_conf_filename, tool_data_table_elems = \
                tool_util.install_tool_data_tables( self.app, tool_shed_repository, tool_index_sample_files )
            if tool_data_table_elems:
                self.app.tool_data_tables.add_new_entries_from_config_file( tool_data_table_conf_filename,
                                                                            None,
                                                                            self.app.config.shed_tool_data_table_config,
                                                                            persist=True )
        if 'tools' in metadata_dict:
            tool_panel_dict = tool_util.generate_tool_panel_dict_for_new_install( metadata_dict[ 'tools' ], tool_section )
            sample_files = metadata_dict.get( 'sample_files', [] )
            tool_index_sample_files = tool_util.get_tool_index_sample_files( sample_files )
            tool_util.copy_sample_files( self.app, tool_index_sample_files, tool_path=tool_path )
            sample_files_copied = [ str( s ) for s in tool_index_sample_files ]
            repository_tools_tups = suc.get_repository_tools_tups( self.app, metadata_dict )
            if repository_tools_tups:
                # Handle missing data table entries for tool parameters that are dynamically generated select lists.
                repository_tools_tups = tool_util.handle_missing_data_table_entry( self.app,
                                                                                   relative_install_dir,
                                                                                   tool_path,
                                                                                   repository_tools_tups )
                # Handle missing index files for tool parameters that are dynamically generated select lists.
                repository_tools_tups, sample_files_copied = tool_util.handle_missing_index_file( self.app,
                                                                                                  tool_path,
                                                                                                  sample_files,
                                                                                                  repository_tools_tups,
                                                                                                  sample_files_copied )
                # Copy remaining sample files included in the repository to the ~/tool-data directory of the
                # local Galaxy instance.
                tool_util.copy_sample_files( self.app, sample_files, tool_path=tool_path, sample_files_copied=sample_files_copied )
                tool_util.add_to_tool_panel( app=self.app,
                                             repository_name=tool_shed_repository.name,
                                             repository_clone_url=repository_clone_url,
                                             changeset_revision=tool_shed_repository.installed_changeset_revision,
                                             repository_tools_tups=repository_tools_tups,
                                             owner=tool_shed_repository.owner,
                                             shed_tool_conf=shed_tool_conf,
                                             tool_panel_dict=tool_panel_dict,
                                             new_install=True )
        if 'data_manager' in metadata_dict:
            new_data_managers = data_manager_util.install_data_managers( self.app,
                                                                         self.app.config.shed_data_manager_config_file,
                                                                         metadata_dict,
                                                                         shed_config_dict,
                                                                         relative_install_dir,
                                                                         tool_shed_repository,
                                                                         repository_tools_tups )
        if 'datatypes' in metadata_dict:
            tool_shed_repository.status = install_model.ToolShedRepository.installation_status.LOADING_PROPRIETARY_DATATYPES
            if not tool_shed_repository.includes_datatypes:
                tool_shed_repository.includes_datatypes = True
            install_model.context.add( tool_shed_repository )
            install_model.context.flush()
            files_dir = relative_install_dir
            if shed_config_dict.get( 'tool_path' ):
                files_dir = os.path.join( shed_config_dict[ 'tool_path' ], files_dir )
            datatypes_config = hg_util.get_config_from_disk( suc.DATATYPES_CONFIG_FILENAME, files_dir )
            # Load data types required by tools.
            converter_path, display_path = \
                datatype_util.alter_config_and_load_prorietary_datatypes( self.app, datatypes_config, files_dir, override=False )
            if converter_path or display_path:
                # Create a dictionary of tool shed repository related information.
                repository_dict = \
                    datatype_util.create_repository_dict_for_proprietary_datatypes( tool_shed=tool_shed,
                                                                                    name=tool_shed_repository.name,
                                                                                    owner=tool_shed_repository.owner,
                                                                                    installed_changeset_revision=tool_shed_repository.installed_changeset_revision,
                                                                                    tool_dicts=metadata_dict.get( 'tools', [] ),
                                                                                    converter_path=converter_path,
                                                                                    display_path=display_path )
            if converter_path:
                # Load proprietary datatype converters
                self.app.datatypes_registry.load_datatype_converters( self.app.toolbox, installed_repository_dict=repository_dict )
            if display_path:
                # Load proprietary datatype display applications
                self.app.datatypes_registry.load_display_applications( installed_repository_dict=repository_dict )

    def handle_tool_shed_repositories( self, installation_dict, using_api=False ):
        # The following installation_dict entries are all required.
        install_repository_dependencies = installation_dict[ 'install_repository_dependencies' ]
        new_tool_panel_section_label = installation_dict[ 'new_tool_panel_section_label' ]
        no_changes_checked = installation_dict[ 'no_changes_checked' ]
        repo_info_dicts = installation_dict[ 'repo_info_dicts' ]
        tool_panel_section_id = installation_dict[ 'tool_panel_section_id' ]
        tool_path = installation_dict[ 'tool_path' ]
        tool_shed_url = installation_dict[ 'tool_shed_url' ]
        rdm = RepositoryDependencyManager( self.app )
        created_or_updated_tool_shed_repositories, tool_panel_section_keys, repo_info_dicts, filtered_repo_info_dicts = \
            rdm.create_repository_dependency_objects( tool_path=tool_path,
                                                      tool_shed_url=tool_shed_url,
                                                      repo_info_dicts=repo_info_dicts,
                                                      install_repository_dependencies=install_repository_dependencies,
                                                      no_changes_checked=no_changes_checked,
                                                      tool_panel_section_id=tool_panel_section_id,
                                                      new_tool_panel_section_label=new_tool_panel_section_label )
        return created_or_updated_tool_shed_repositories, tool_panel_section_keys, repo_info_dicts, filtered_repo_info_dicts

    def initiate_repository_installation( self, installation_dict ):
        install_model = self.app.install_model
        # The following installation_dict entries are all required.
        created_or_updated_tool_shed_repositories = installation_dict[ 'created_or_updated_tool_shed_repositories' ]
        filtered_repo_info_dicts = installation_dict[ 'filtered_repo_info_dicts' ]
        has_repository_dependencies = installation_dict[ 'has_repository_dependencies' ]
        includes_tool_dependencies = installation_dict[ 'includes_tool_dependencies' ]
        includes_tools = installation_dict[ 'includes_tools' ]
        includes_tools_for_display_in_tool_panel = installation_dict[ 'includes_tools_for_display_in_tool_panel' ]
        install_repository_dependencies = installation_dict[ 'install_repository_dependencies' ]
        install_tool_dependencies = installation_dict[ 'install_tool_dependencies' ]
        message = installation_dict[ 'message' ]
        new_tool_panel_section_label = installation_dict[ 'new_tool_panel_section_label' ]
        shed_tool_conf = installation_dict[ 'shed_tool_conf' ]
        status = installation_dict[ 'status' ]
        tool_panel_section_id = installation_dict[ 'tool_panel_section_id' ]
        tool_panel_section_keys = installation_dict[ 'tool_panel_section_keys' ]
        tool_path = installation_dict[ 'tool_path' ]
        tool_shed_url = installation_dict[ 'tool_shed_url' ]
        # Handle contained tools.
        if includes_tools_for_display_in_tool_panel and ( new_tool_panel_section_label or tool_panel_section_id ):
            tool_panel_section_key, tool_section = \
                tool_util.handle_tool_panel_section( self.app.toolbox,
                                                     tool_panel_section_id=tool_panel_section_id,
                                                     new_tool_panel_section_label=new_tool_panel_section_label )
        else:
            tool_panel_section_key = None
            tool_section = None
        encoded_repository_ids = [ self.app.security.encode_id( tsr.id ) for tsr in created_or_updated_tool_shed_repositories ]
        new_kwd = dict( includes_tools=includes_tools,
                        includes_tools_for_display_in_tool_panel=includes_tools_for_display_in_tool_panel,
                        has_repository_dependencies=has_repository_dependencies,
                        install_repository_dependencies=install_repository_dependencies,
                        includes_tool_dependencies=includes_tool_dependencies,
                        install_tool_dependencies=install_tool_dependencies,
                        message=message,
                        repo_info_dicts=filtered_repo_info_dicts,
                        shed_tool_conf=shed_tool_conf,
                        status=status,
                        tool_path=tool_path,
                        tool_panel_section_keys=tool_panel_section_keys,
                        tool_shed_repository_ids=encoded_repository_ids,
                        tool_shed_url=tool_shed_url )
        encoded_kwd = encoding_util.tool_shed_encode( new_kwd )
        tsr_ids = [ r.id  for r in created_or_updated_tool_shed_repositories  ]
        tool_shed_repositories = []
        for tsr_id in tsr_ids:
            tsr = install_model.context.query( install_model.ToolShedRepository ).get( tsr_id )
            tool_shed_repositories.append( tsr )
        clause_list = []
        for tsr_id in tsr_ids:
            clause_list.append( install_model.ToolShedRepository.table.c.id == tsr_id )
        query = install_model.context.query( install_model.ToolShedRepository ).filter( or_( *clause_list ) )
        return encoded_kwd, query, tool_shed_repositories, encoded_repository_ids

    def install( self, tool_shed_url, name, owner, changeset_revision, install_options ):
        # Get all of the information necessary for installing the repository from the specified tool shed.
        repository_revision_dict, repo_info_dicts = self.__get_install_info_from_tool_shed( tool_shed_url,
                                                                                            name,
                                                                                            owner,
                                                                                            changeset_revision )
        installed_tool_shed_repositories = self.__install_repositories( repository_revision_dict,
                                                                        repo_info_dicts,
                                                                        install_options )
        return installed_tool_shed_repositories

    def __install_repositories( self, tool_shed_url, repository_revision_dict, repo_info_dicts, install_options ):
        # Keep track of all repositories that are installed - there may be more than one if repository dependencies are installed.
        installed_tool_shed_repositories = []
        try:
            has_repository_dependencies = repository_revision_dict[ 'has_repository_dependencies' ]
        except:
            raise exceptions.InternalServerError( "Tool shed response missing required parameter 'has_repository_dependencies'." )
        try:
            includes_tools = repository_revision_dict[ 'includes_tools' ]
        except:
            raise exceptions.InternalServerError( "Tool shed response missing required parameter 'includes_tools'." )
        try:
            includes_tool_dependencies = repository_revision_dict[ 'includes_tool_dependencies' ]
        except:
            raise exceptions.InternalServerError( "Tool shed response missing required parameter 'includes_tool_dependencies'." )
        try:
            includes_tools_for_display_in_tool_panel = repository_revision_dict[ 'includes_tools_for_display_in_tool_panel' ]
        except:
            raise exceptions.InternalServerError( "Tool shed response missing required parameter 'includes_tools_for_display_in_tool_panel'." )
        # Get the information about the Galaxy components (e.g., tool pane section, tool config file, etc) that will contain the repository information.
        install_repository_dependencies = install_options.get( 'install_repository_dependencies', False )
        install_tool_dependencies = install_options.get( 'install_tool_dependencies', False )
        if install_tool_dependencies:
            if self.app.config.tool_dependency_dir is None:
                no_tool_dependency_dir_message = "Tool dependencies can be automatically installed only if you set "
                no_tool_dependency_dir_message += "the value of your 'tool_dependency_dir' setting in your Galaxy "
                no_tool_dependency_dir_message += "configuration file (universe_wsgi.ini) and restart your Galaxy server.  "
                raise exceptions.ConfigDoesNotAllowException( no_tool_dependency_dir_message )
        new_tool_panel_section_label = install_options.get( 'new_tool_panel_section_label', '' )
        shed_tool_conf = install_options.get( 'shed_tool_conf', None )
        if shed_tool_conf:
            # Get the tool_path setting.
            index, shed_conf_dict = suc.get_shed_tool_conf_dict( self.app, shed_tool_conf )
            tool_path = shed_conf_dict[ 'tool_path' ]
        else:
            # Pick a semi-random shed-related tool panel configuration file and get the tool_path setting.
            for shed_config_dict in self.app.toolbox.shed_tool_confs:
                # Don't use migrated_tools_conf.xml.
                if shed_config_dict[ 'config_filename' ] != self.app.config.migrated_tools_config:
                    break
            shed_tool_conf = shed_config_dict[ 'config_filename' ]
            tool_path = shed_config_dict[ 'tool_path' ]
        if not shed_tool_conf:
            raise exceptions.RequestParameterMissingException( "Missing required parameter 'shed_tool_conf'." )
        tool_panel_section_id = install_options.get( 'tool_panel_section_id', '' )
        if tool_panel_section_id not in [ None, '' ]:
            if tool_panel_section_id not in self.app.toolbox.tool_panel:
                fixed_tool_panel_section_id = 'section_%s' % tool_panel_section_id
                if fixed_tool_panel_section_id in self.app.toolbox.tool_panel:
                    tool_panel_section_id = fixed_tool_panel_section_id
                else:
                    tool_panel_section_id = ''
        else:
            tool_panel_section_id = ''
        # Build the dictionary of information necessary for creating tool_shed_repository database records
        # for each repository being installed.
        installation_dict = dict( install_repository_dependencies=install_repository_dependencies,
                                  new_tool_panel_section_label=new_tool_panel_section_label,
                                  no_changes_checked=False,
                                  repo_info_dicts=repo_info_dicts,
                                  tool_panel_section_id=tool_panel_section_id,
                                  tool_path=tool_path,
                                  tool_shed_url=tool_shed_url )
        # Create the tool_shed_repository database records and gather additional information for repository installation.
        created_or_updated_tool_shed_repositories, tool_panel_section_keys, repo_info_dicts, filtered_repo_info_dicts = \
            self.handle_tool_shed_repositories( installation_dict, using_api=True )
        if created_or_updated_tool_shed_repositories:
            # Build the dictionary of information necessary for installing the repositories.
            installation_dict = dict( created_or_updated_tool_shed_repositories=created_or_updated_tool_shed_repositories,
                                      filtered_repo_info_dicts=filtered_repo_info_dicts,
                                      has_repository_dependencies=has_repository_dependencies,
                                      includes_tool_dependencies=includes_tool_dependencies,
                                      includes_tools=includes_tools,
                                      includes_tools_for_display_in_tool_panel=includes_tools_for_display_in_tool_panel,
                                      install_repository_dependencies=install_repository_dependencies,
                                      install_tool_dependencies=install_tool_dependencies,
                                      message='',
                                      new_tool_panel_section_label=new_tool_panel_section_label,
                                      shed_tool_conf=shed_tool_conf,
                                      status='done',
                                      tool_panel_section_id=tool_panel_section_id,
                                      tool_panel_section_keys=tool_panel_section_keys,
                                      tool_path=tool_path,
                                      tool_shed_url=tool_shed_url )
            # Prepare the repositories for installation.  Even though this method receives a single combination
            # of tool_shed_url, name, owner and changeset_revision, there may be multiple repositories for installation
            # at this point because repository dependencies may have added additional repositories for installation
            # along with the single specified repository.
            encoded_kwd, query, tool_shed_repositories, encoded_repository_ids = \
                initiate_repository_installation( self.app, installation_dict )
            # Some repositories may have repository dependencies that are required to be installed before the
            # dependent repository, so we'll order the list of tsr_ids to ensure all repositories install in
            # the required order.
            tsr_ids = [ self.app.security.encode_id( tool_shed_repository.id ) for tool_shed_repository in tool_shed_repositories ]
            ordered_tsr_ids, ordered_repo_info_dicts, ordered_tool_panel_section_keys = \
                self.order_components_for_installation( tsr_ids, repo_info_dicts, tool_panel_section_keys=tool_panel_section_keys )
            # Install the repositories, keeping track of each one for later display.
            for index, tsr_id in enumerate( ordered_tsr_ids ):
                install_model = self.app.install_model
                tool_shed_repository = install_model.context.query( install_model.ToolShedRepository ) \
                                                            .get( self.app.security.decode_id( tsr_id ) )
                if tool_shed_repository.status in [ install_model.ToolShedRepository.installation_status.NEW,
                                                    install_model.ToolShedRepository.installation_status.UNINSTALLED ]:
                    repo_info_dict = ordered_repo_info_dicts[ index ]
                    tool_panel_section_key = ordered_tool_panel_section_keys[ index ]
                    self.install_tool_shed_repository( tool_shed_repository,
                                                       repo_info_dict,
                                                       tool_panel_section_key,
                                                       shed_tool_conf,
                                                       tool_path,
                                                       install_tool_dependencies,
                                                       reinstalling=False )
                    installed_tool_shed_repositories.append( tool_shed_repository )
        else:
            # We're attempting to install more than 1 repository, and all of them have already been installed.
            raise exceptions.RequestParameterInvalidException( 'All repositories that you are attempting to install have been previously installed.' )
        return installed_tool_shed_repositories

    def install_tool_shed_repository( self, tool_shed_repository, repo_info_dict, tool_panel_section_key, shed_tool_conf, tool_path,
                                      install_tool_dependencies, reinstalling=False ):
        install_model = self.app.install_model
        if tool_panel_section_key:
            try:
                tool_section = self.app.toolbox.tool_panel[ tool_panel_section_key ]
            except KeyError:
                log.debug( 'Invalid tool_panel_section_key "%s" specified.  Tools will be loaded outside of sections in the tool panel.',
                           str( tool_panel_section_key ) )
                tool_section = None
        else:
            tool_section = None
        if isinstance( repo_info_dict, basestring ):
            repo_info_dict = encoding_util.tool_shed_decode( repo_info_dict )
        # Clone each repository to the configured location.
        suc.update_tool_shed_repository_status( self.app,
                                                tool_shed_repository,
                                                install_model.ToolShedRepository.installation_status.CLONING )
        repo_info_tuple = repo_info_dict[ tool_shed_repository.name ]
        description, repository_clone_url, changeset_revision, ctx_rev, repository_owner, repository_dependencies, tool_dependencies = repo_info_tuple
        relative_clone_dir = suc.generate_tool_shed_repository_install_dir( repository_clone_url,
                                                                            tool_shed_repository.installed_changeset_revision )
        relative_install_dir = os.path.join( relative_clone_dir, tool_shed_repository.name )
        install_dir = os.path.join( tool_path, relative_install_dir )
        cloned_ok, error_message = hg_util.clone_repository( repository_clone_url, os.path.abspath( install_dir ), ctx_rev )
        if cloned_ok:
            if reinstalling:
                # Since we're reinstalling the repository we need to find the latest changeset revision to
                # which it can be updated.
                changeset_revision_dict = self.app.update_repository_manager.get_update_to_changeset_revision_and_ctx_rev( tool_shed_repository )
                current_changeset_revision = changeset_revision_dict.get( 'changeset_revision', None )
                current_ctx_rev = changeset_revision_dict.get( 'ctx_rev', None )
                if current_ctx_rev != ctx_rev:
                    repo = hg_util.get_repo_for_repository( self.app,
                                                            repository=None,
                                                            repo_path=os.path.abspath( install_dir ),
                                                            create=False )
                    hg_util.pull_repository( repo, repository_clone_url, current_changeset_revision )
                    hg_util.update_repository( repo, ctx_rev=current_ctx_rev )
            self.handle_repository_contents( tool_shed_repository=tool_shed_repository,
                                             tool_path=tool_path,
                                             repository_clone_url=repository_clone_url,
                                             relative_install_dir=relative_install_dir,
                                             tool_shed=tool_shed_repository.tool_shed,
                                             tool_section=tool_section,
                                             shed_tool_conf=shed_tool_conf,
                                             reinstalling=reinstalling )
            install_model.context.refresh( tool_shed_repository )
            metadata = tool_shed_repository.metadata
            if 'tools' in metadata:
                # Get the tool_versions from the tool shed for each tool in the installed change set.
                suc.update_tool_shed_repository_status( self.app,
                                                        tool_shed_repository,
                                                        install_model.ToolShedRepository.installation_status.SETTING_TOOL_VERSIONS )
                tool_shed_url = common_util.get_tool_shed_url_from_tool_shed_registry( self.app, str( tool_shed_repository.tool_shed ) )
                params = '?name=%s&owner=%s&changeset_revision=%s' % ( str( tool_shed_repository.name ),
                                                                       str( tool_shed_repository.owner ),
                                                                       str( tool_shed_repository.changeset_revision ) )
                url = common_util.url_join( tool_shed_url,
                                            '/repository/get_tool_versions%s' % params )
                text = common_util.tool_shed_get( self.app, tool_shed_url, url )
                if text:
                    tool_version_dicts = json.loads( text )
                    tool_util.handle_tool_versions( self.app, tool_version_dicts, tool_shed_repository )
                else:
                    if not error_message:
                        error_message = ""
                    error_message += "Version information for the tools included in the <b>%s</b> repository is missing.  " % tool_shed_repository.name
                    error_message += "Reset all of this repository's metadata in the tool shed, then set the installed tool versions "
                    error_message += "from the installed repository's <b>Repository Actions</b> menu.  "
            if install_tool_dependencies and tool_shed_repository.tool_dependencies and 'tool_dependencies' in metadata:
                work_dir = tempfile.mkdtemp( prefix="tmp-toolshed-itsr" )
                # Install tool dependencies.
                suc.update_tool_shed_repository_status( self.app,
                                                        tool_shed_repository,
                                                        install_model.ToolShedRepository.installation_status.INSTALLING_TOOL_DEPENDENCIES )
                # Get the tool_dependencies.xml file from the repository.
                tool_dependencies_config = hg_util.get_config_from_disk( 'tool_dependencies.xml', install_dir )
                itdm = InstallToolDependencyManager( self.app )
                installed_tool_dependencies = itdm.install_specified_tool_dependencies( tool_shed_repository=tool_shed_repository,
                                                                                        tool_dependencies_config=tool_dependencies_config,
                                                                                        tool_dependencies=tool_shed_repository.tool_dependencies,
                                                                                        from_tool_migration_manager=False )
                basic_util.remove_dir( work_dir )
            suc.update_tool_shed_repository_status( self.app,
                                                    tool_shed_repository,
                                                    install_model.ToolShedRepository.installation_status.INSTALLED )
            if self.app.config.manage_dependency_relationships:
                # Add the installed repository and any tool dependencies to the in-memory dictionaries in the installed_repository_manager.
                self.app.installed_repository_manager.handle_repository_install( tool_shed_repository )
        else:
            # An error occurred while cloning the repository, so reset everything necessary to enable another attempt.
            suc.set_repository_attributes( self.app,
                                           tool_shed_repository,
                                           status=install_model.ToolShedRepository.installation_status.ERROR,
                                           error_message=error_message,
                                           deleted=False,
                                           uninstalled=False,
                                           remove_from_disk=True )

    def merge_containers_dicts_for_new_install( self, containers_dicts ):
        """
        When installing one or more tool shed repositories for the first time, the received list of
        containers_dicts contains a containers_dict for each repository being installed.  Since the
        repositories are being installed for the first time, all entries are None except the repository
        dependencies and tool dependencies.  The entries for missing dependencies are all None since
        they have previously been merged into the installed dependencies.  This method will merge the
        dependencies entries into a single container and return it for display.
        """
        new_containers_dict = dict( readme_files=None,
                                    datatypes=None,
                                    missing_repository_dependencies=None,
                                    repository_dependencies=None,
                                    missing_tool_dependencies=None,
                                    tool_dependencies=None,
                                    invalid_tools=None,
                                    valid_tools=None,
                                    workflows=None )
        if containers_dicts:
            lock = threading.Lock()
            lock.acquire( True )
            try:
                repository_dependencies_root_folder = None
                tool_dependencies_root_folder = None
                # Use a unique folder id (hopefully the following is).
                folder_id = 867
                for old_container_dict in containers_dicts:
                    # Merge repository_dependencies.
                    old_container_repository_dependencies_root = old_container_dict[ 'repository_dependencies' ]
                    if old_container_repository_dependencies_root:
                        if repository_dependencies_root_folder is None:
                            repository_dependencies_root_folder = container_util.Folder( id=folder_id,
                                                                                         key='root',
                                                                                         label='root',
                                                                                         parent=None )
                            folder_id += 1
                            repository_dependencies_folder = container_util.Folder( id=folder_id,
                                                                                    key='merged',
                                                                                    label='Repository dependencies',
                                                                                    parent=repository_dependencies_root_folder )
                            folder_id += 1
                        # The old_container_repository_dependencies_root will be a root folder containing a single sub_folder.
                        old_container_repository_dependencies_folder = old_container_repository_dependencies_root.folders[ 0 ]
                        # Change the folder id so it won't confict with others being merged.
                        old_container_repository_dependencies_folder.id = folder_id
                        folder_id += 1
                        repository_components_tuple = container_util.get_components_from_key( old_container_repository_dependencies_folder.key )
                        components_list = suc.extract_components_from_tuple( repository_components_tuple )
                        name = components_list[ 1 ]
                        # Generate the label by retrieving the repository name.
                        old_container_repository_dependencies_folder.label = str( name )
                        repository_dependencies_folder.folders.append( old_container_repository_dependencies_folder )
                    # Merge tool_dependencies.
                    old_container_tool_dependencies_root = old_container_dict[ 'tool_dependencies' ]
                    if old_container_tool_dependencies_root:
                        if tool_dependencies_root_folder is None:
                            tool_dependencies_root_folder = container_util.Folder( id=folder_id,
                                                                                   key='root',
                                                                                   label='root',
                                                                                   parent=None )
                            folder_id += 1
                            tool_dependencies_folder = container_util.Folder( id=folder_id,
                                                                              key='merged',
                                                                              label='Tool dependencies',
                                                                              parent=tool_dependencies_root_folder )
                            folder_id += 1
                        else:
                            td_list = [ td.listify for td in tool_dependencies_folder.tool_dependencies ]
                            # The old_container_tool_dependencies_root will be a root folder containing a single sub_folder.
                            old_container_tool_dependencies_folder = old_container_tool_dependencies_root.folders[ 0 ]
                            for td in old_container_tool_dependencies_folder.tool_dependencies:
                                if td.listify not in td_list:
                                    tool_dependencies_folder.tool_dependencies.append( td )
                if repository_dependencies_root_folder:
                    repository_dependencies_root_folder.folders.append( repository_dependencies_folder )
                    new_containers_dict[ 'repository_dependencies' ] = repository_dependencies_root_folder
                if tool_dependencies_root_folder:
                    tool_dependencies_root_folder.folders.append( tool_dependencies_folder )
                    new_containers_dict[ 'tool_dependencies' ] = tool_dependencies_root_folder
            except Exception, e:
                log.debug( "Exception in merge_containers_dicts_for_new_install: %s" % str( e ) )
            finally:
                lock.release()
        return new_containers_dict

    def merge_missing_repository_dependencies_to_installed_container( self, containers_dict ):
        """Merge the list of missing repository dependencies into the list of installed repository dependencies."""
        missing_rd_container_root = containers_dict.get( 'missing_repository_dependencies', None )
        if missing_rd_container_root:
            # The missing_rd_container_root will be a root folder containing a single sub_folder.
            missing_rd_container = missing_rd_container_root.folders[ 0 ]
            installed_rd_container_root = containers_dict.get( 'repository_dependencies', None )
            # The installed_rd_container_root will be a root folder containing a single sub_folder.
            if installed_rd_container_root:
                installed_rd_container = installed_rd_container_root.folders[ 0 ]
                installed_rd_container.label = 'Repository dependencies'
                for index, rd in enumerate( missing_rd_container.repository_dependencies ):
                    # Skip the header row.
                    if index == 0:
                        continue
                    installed_rd_container.repository_dependencies.append( rd )
                installed_rd_container_root.folders = [ installed_rd_container ]
                containers_dict[ 'repository_dependencies' ] = installed_rd_container_root
            else:
                # Change the folder label from 'Missing repository dependencies' to be 'Repository dependencies' for display.
                root_container = containers_dict[ 'missing_repository_dependencies' ]
                for sub_container in root_container.folders:
                    # There should only be 1 sub-folder.
                    sub_container.label = 'Repository dependencies'
                containers_dict[ 'repository_dependencies' ] = root_container
        containers_dict[ 'missing_repository_dependencies' ] = None
        return containers_dict

    def merge_missing_tool_dependencies_to_installed_container( self, containers_dict ):
        """ Merge the list of missing tool dependencies into the list of installed tool dependencies."""
        missing_td_container_root = containers_dict.get( 'missing_tool_dependencies', None )
        if missing_td_container_root:
            # The missing_td_container_root will be a root folder containing a single sub_folder.
            missing_td_container = missing_td_container_root.folders[ 0 ]
            installed_td_container_root = containers_dict.get( 'tool_dependencies', None )
            # The installed_td_container_root will be a root folder containing a single sub_folder.
            if installed_td_container_root:
                installed_td_container = installed_td_container_root.folders[ 0 ]
                installed_td_container.label = 'Tool dependencies'
                for index, td in enumerate( missing_td_container.tool_dependencies ):
                    # Skip the header row.
                    if index == 0:
                        continue
                    installed_td_container.tool_dependencies.append( td )
                installed_td_container_root.folders = [ installed_td_container ]
                containers_dict[ 'tool_dependencies' ] = installed_td_container_root
            else:
                # Change the folder label from 'Missing tool dependencies' to be 'Tool dependencies' for display.
                root_container = containers_dict[ 'missing_tool_dependencies' ]
                for sub_container in root_container.folders:
                    # There should only be 1 subfolder.
                    sub_container.label = 'Tool dependencies'
                containers_dict[ 'tool_dependencies' ] = root_container
        containers_dict[ 'missing_tool_dependencies' ] = None
        return containers_dict

    def order_components_for_installation( self, tsr_ids, repo_info_dicts, tool_panel_section_keys ):
        """
        Some repositories may have repository dependencies that are required to be installed
        before the dependent repository.  This method will inspect the list of repositories
        about to be installed and make sure to order them appropriately.  For each repository
        about to be installed, if required repositories are not contained in the list of repositories
        about to be installed, then they are not considered.  Repository dependency definitions
        that contain circular dependencies should not result in an infinite loop, but obviously
        prior installation will not be handled for one or more of the repositories that require
        prior installation.
        """
        ordered_tsr_ids = []
        ordered_repo_info_dicts = []
        ordered_tool_panel_section_keys = []
        # Create a dictionary whose keys are the received tsr_ids and whose values are a list of
        # tsr_ids, each of which is contained in the received list of tsr_ids and whose associated
        # repository must be installed prior to the repository associated with the tsr_id key.
        prior_install_required_dict = suc.get_prior_import_or_install_required_dict( self.app,
                                                                                     tsr_ids,
                                                                                     repo_info_dicts )
        processed_tsr_ids = []
        while len( processed_tsr_ids ) != len( prior_install_required_dict.keys() ):
            tsr_id = suc.get_next_prior_import_or_install_required_dict_entry( prior_install_required_dict,
                                                                               processed_tsr_ids )
            processed_tsr_ids.append( tsr_id )
            # Create the ordered_tsr_ids, the ordered_repo_info_dicts and the ordered_tool_panel_section_keys lists.
            if tsr_id not in ordered_tsr_ids:
                prior_install_required_ids = prior_install_required_dict[ tsr_id ]
                for prior_install_required_id in prior_install_required_ids:
                    if prior_install_required_id not in ordered_tsr_ids:
                        # Install the associated repository dependency first.
                        prior_repo_info_dict, prior_tool_panel_section_key = \
                            self.get_repository_components_for_installation( prior_install_required_id,
                                                                             tsr_ids,
                                                                             repo_info_dicts,
                                                                             tool_panel_section_keys=tool_panel_section_keys )
                        ordered_tsr_ids.append( prior_install_required_id )
                        ordered_repo_info_dicts.append( prior_repo_info_dict )
                        ordered_tool_panel_section_keys.append( prior_tool_panel_section_key )
                repo_info_dict, tool_panel_section_key = \
                    self.get_repository_components_for_installation( tsr_id,
                                                                     tsr_ids,
                                                                     repo_info_dicts,
                                                                     tool_panel_section_keys=tool_panel_section_keys )
                ordered_tsr_ids.append( tsr_id )
                ordered_repo_info_dicts.append( repo_info_dict )
                ordered_tool_panel_section_keys.append( tool_panel_section_key )
        return ordered_tsr_ids, ordered_repo_info_dicts, ordered_tool_panel_section_keys

    def populate_containers_dict_for_new_install( self, tool_shed_url, tool_path, readme_files_dict, installed_repository_dependencies,
                                                  missing_repository_dependencies, installed_tool_dependencies, missing_tool_dependencies,
                                                  updating=False ):
        """
        Return the populated containers for a repository being installed for the first time or for an installed repository
        that is being updated and the updates include newly defined repository (and possibly tool) dependencies.
        """
        installed_tool_dependencies, missing_tool_dependencies = \
            tool_dependency_util.populate_tool_dependencies_dicts( app=self.app,
                                                                   tool_shed_url=tool_shed_url,
                                                                   tool_path=tool_path,
                                                                   repository_installed_tool_dependencies=installed_tool_dependencies,
                                                                   repository_missing_tool_dependencies=missing_tool_dependencies,
                                                                   required_repo_info_dicts=None )
        # Most of the repository contents are set to None since we don't yet know what they are.
        containers_dict = \
            container_util.build_repository_containers_for_galaxy( app=self.app,
                                                                   repository=None,
                                                                   datatypes=None,
                                                                   invalid_tools=None,
                                                                   missing_repository_dependencies=missing_repository_dependencies,
                                                                   missing_tool_dependencies=missing_tool_dependencies,
                                                                   readme_files_dict=readme_files_dict,
                                                                   repository_dependencies=installed_repository_dependencies,
                                                                   tool_dependencies=installed_tool_dependencies,
                                                                   valid_tools=None,
                                                                   workflows=None,
                                                                   valid_data_managers=None,
                                                                   invalid_data_managers=None,
                                                                   data_managers_errors=None,
                                                                   new_install=True,
                                                                   reinstalling=False )
        if not updating:
            # If we installing a new repository and not updaing an installed repository, we can merge
            # the missing_repository_dependencies container contents to the installed_repository_dependencies
            # container.  When updating an installed repository, merging will result in losing newly defined
            # dependencies included in the updates.
            containers_dict = self.merge_missing_repository_dependencies_to_installed_container( containers_dict )
            # Merge the missing_tool_dependencies container contents to the installed_tool_dependencies container.
            containers_dict = self.merge_missing_tool_dependencies_to_installed_container( containers_dict )
        return containers_dict