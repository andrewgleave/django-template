import os

from fabric.api import *
from fabric.contrib.project import rsync_project
from fabric.contrib import files, console
from fabric import utils
from fabric.operations import prompt
from fabric.decorators import hosts
from fabric.contrib import django


'''This fabfile assumes the project follows the project structure outlined in _configure()
    e.g. 
    
    django-template/web/conf/nginx
    django-template/web/conf/supervisord
    django-template/web/django-template
'''

env.port = 22
env.home = '/home/django-template/'
env.project = 'django-template'
env.nginx_root = '/etc/nginx/sites-enabled/'
env.supervisord_root = '/etc/supervisord/conf.d/'
env.clone_command = 'git clone git@github.com:andrewgleave/django-template.git'


def _configure():
    env.root = os.path.join(env.home, 'www', env.environment)                                           #/home/django-template/www/<environment>/
    env.virtualenvs_root = os.path.join(env.home, 'virtualenvs')                                        #/home/django-template/virtualenvs/
    env.project_virtualenv_root = os.path.join(env.virtualenvs_root, env.environment)                   #/home/django-template/virtualenvs/<environment>/
    env.virtualenv_activate = 'source %(project_virtualenv_root)s/bin/activate' % env                   #source /home/django-template/virtualenvs/<environment>/bin/activate
    env.code_root = os.path.join(env.root, env.project)                                                 #/home/django-template/www/<environment>/django-template/
    env.web_root = os.path.join(env.code_root, 'web')                                                   #/home/django-template/www/<environment>/django-template/
    env.settings_file = '%(project)s.settings_%(environment)s' % env                                    #django-template.settings_<environment>
    #/home/django-template/www/<environment>/django-template/conf/nginx/django-template-<environment>.conf
    env.nginx_conf_path = os.path.join(env.web_root, 'conf', 'nginx', 
                                                '%(project)s-%(environment)s.conf' % env) 
    #/home/django-template/www/<environment>/django-template/conf/supervisord/django-template-<environment>.conf
    env.supervisord_conf_path = os.path.join(env.web_root, 'conf', 
                                                'supervisord', 
                                                '%(project)s-%(environment)s.conf' % env)
    #/home/django-template/www/<environment>/django-template/conf/pip/requirements.txt
    env.pip_requirements_path = os.path.join(env.web_root, 'conf', 'pip', 'requirements.txt')
    env.checkout_command = 'git checkout %(branch_name)s && git pull' % env
    env.project_root = os.path.join(env.web_root, env.project)
    env.static_asset_root = os.path.join(env.web_root, 'static')
    
@task
def staging():
    '''Use staging environment'''
    env.user = 'django-template'
    env.environment = 'staging'
    env.hosts = []
    env.branch_name = 'develop'
    env.redis_port = 6379
    _configure()

@task
def production():
    '''Use production environment'''
    env.user = 'django-template'
    env.environment = 'production'
    env.hosts = ['']
    env.branch_name = 'master'
    env.redis_port = 6379
    _configure()

@task
def bootstrap():
    '''Create nessary directories, virtualenvs, installs code'''
    require('root', provided_by=('staging', 'production'))
    run('mkdir -p %(root)s/{log,run,var}' % env)
    run('touch %(root)s/log/{application.log,watched.log}' % env)
    with settings(user='sudouser'):
        sudo('chmod -R 755 %(root)s/run' % env)
    create_virtualenv()
    checkout()
    update_requirements()
    #create_db()
    syncdb()
    update_supervisord()
    update_nginx()

@task
def checkout():
    '''Checkout/update code from repository'''
    require('code_root', provided_by=('staging', 'production'))
    with cd('~'):
        if not files.exists(env.code_root): #checkout code
            run('mkdir -p %(code_root)s' % env)
            run('%(clone_command)s  %(code_root)s' % env)
        else:
            if env.environment == 'production':
                if not console.confirm('Are you sure you want to update the production code?', default=False):
                    utils.abort('Production code update aborted.')
        with cd(env.code_root):
            run(env.checkout_command) #co and pull

@task
def create_virtualenv():
    '''Create virtualenvs directory and install virtualenv'''
    require('virtualenvs_root', provided_by=('staging', 'production'))
    with cd('~'):
        run('mkdir -p %(virtualenvs_root)s' % env)
        command = ['virtualenv']
        if files.exists(env.project_virtualenv_root):
            if console.confirm('Clear out the current virtual env?', default=False):
                command.append('--clear')
        command.append('%(project_virtualenv_root)s' % env)
        run(' '.join(command))

@task
def update_requirements():
    '''Update project dependencies from requirements.txt'''
    require('code_root', provided_by=('staging', 'production'))
    if not files.exists(env.pip_requirements_path):
        utils.puts('No requirements file found. Skipping')
        return 0 #continue
    with cd('~'):
        with prefix(env.virtualenv_activate):
            run('pip install -r %s' % env.pip_requirements_path)
            run('deactivate')

@task()
def deploy():
    '''Full deploy. Bootstrap must be run prior to this on a new installation'''
    require('code_root', provided_by=('staging', 'production'))
    if env.environment == 'production':
        if not console.confirm('Are you sure you want to deploy production?',
                               default=False):
            utils.abort('Production deployment aborted.')
    if not files.exists(env.code_root):
        utils.abort('Project not bootstrapped. Run bootstrap before deploy.')
    checkout()
    with prefix(env.virtualenv_activate):
        migrate()
        collect_static()
        run('deactivate')
    restart()

@task()
def syncdb():
    '''Syncdb. Migrations are performed by migrate'''
    require('code_root', provided_by=('staging', 'production'))
    with prefix(env.virtualenv_activate):
        run('python %(web_root)s/manage.py syncdb --noinput --settings=%(settings_file)s' % env)
        run('deactivate')

@task()
def migrate():
    '''Perform migrations'''
    require('code_root', provided_by=('staging', 'production'))
    with prefix(env.virtualenv_activate):
        run('python %(web_root)s/manage.py migrate --noinput --settings=%(settings_file)s' % env)
        run('deactivate')

#supervisor
@task
def update_supervisord():
    '''Update supervisord configuration'''
    require('root', provided_by=('staging', 'production'))
    if not files.exists(env.supervisord_conf_path):
        utils.abort('update_supervisord failed: No configuration file can be found for this environment')
    with settings(user='sudouser'):
        sudo('cp %(supervisord_conf_path)s %(supervisord_root)s' % env)
        sudo('supervisorctl update')
    
@task
def restart():
    '''Restart uWSGI process'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl restart %(project)s-%(environment)s' % env)

@task
def stop():
    '''Stop uWSGI process'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl stop %(project)s-%(environment)s' % env)

@task
def start():
    '''Start uWSGI process'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl start %(project)s-%(environment)s' % env)

#Nginx
@task
def update_nginx():
    '''Update Nginx configuration'''
    require('root', provided_by=('staging', 'production'))
    if not files.exists(env.nginx_conf_path):
        utils.abort('update_nginx failed: No configuration file can be found for this environment')
    with settings(user='sudouser'):
        sudo('cp %(nginx_conf_path)s %(nginx_root)s' % env)
        reload_nginx()

@task
def reload_nginx():
    '''Reload Nginx configuration'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('/etc/init.d/nginx reload')

@task
def restart_nginx():
    '''Restart Nginx'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('/etc/init.d/nginx restart')

#Redis
@task
def start_redis():
    '''Start Redis'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl start %(project)s-%(environment)s-redis' % env)

@task
def stop_redis():
    '''Start Redis'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl stop %(project)s-%(environment)s-redis' % env)

@task
def restart_redis():
    '''Restart Redis'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl restart %(project)s-%(environment)s-redis' % env)

@task
def start_celeryd():
    '''Start Celeryd'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl start %(project)s-%(environment)s-celeryd' % env)

@task
def stop_celeryd():
    '''Stop Celeryd'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        sudo('supervisorctl stop %(project)s-%(environment)s-celeryd' % env)

@task
def restart_celeryd():
    '''Restart Celeryd'''
    require('root', provided_by=('staging', 'production'))
    with settings(user='sudouser'):
        #remove task results (DB 1)
        run('echo "FLUSHDB" | redis-cli -d 1 -p %(redis_port)s' % env)
        sudo('supervisorctl restart %(project)s-%(environment)s-celeryd' % env)

#django
@task
def collect_static():
    '''Collect static files and compress'''
    require('code_root', provided_by=('staging', 'production'))
    with prefix(env.virtualenv_activate):
        run('python %(web_root)s/manage.py collectstatic -v0 --noinput --settings=%(settings_file)s' % env)
        if not files.exists('%(static_asset_root)s/CACHE/img' % env):
            run('ln -s %(static_asset_root)s/bootstrap/img %(static_asset_root)s/CACHE/img' % env)

@task
def load_fixture():
    '''Load a fixture'''
    require('code_root', provided_by=('staging', 'production'))
    with prefix(env.virtualenv_activate):
        fixture_name = prompt('Enter the fixture name (not the path, just the name):', default='')
        if not fixture_name:
            utils.abort('Load fixture aborted.')
        proxy = {'web_root': env.web_root, 'settings_file': env.settings_file, 'project_root': env.project_root, 'fixture_name': fixture_name}
        run('python %(web_root)s/manage.py loaddata --settings=%(settings_file)s %(project_root)s/core/fixtures/%(fixture_name)s' % proxy)

@task
def create_superuser():
    '''Collect static files'''
    require('code_root', provided_by=('staging', 'production'))
    with prefix(env.virtualenv_activate):
        run('python %(web_root)s/manage.py createsuperuser --settings=%(settings_file)s' % env)

#db
@task
def create_db():
    require('root', provided_by=('staging', 'production'))
    with prefix(env.virtualenv_activate):
        django.settings_module('%(project)s.settings_%(environment)s' % env)
        from django.conf import settings as django_settings
        user = django_settings.DATABASES['default']['USER']
        password = django_settings.DATABASES['default']['PASSWORD']
        name = django_settings.DATABASES['default']['NAME']
        with settings(user='sudouser', warn_only=True):
            sudo('psql -c "CREATE USER %s WITH NOCREATEDB NOCREATEUSER ENCRYPTED PASSWORD E\'%s\'"' % (user, password), user='postgres')
            sudo('psql -c "CREATE DATABASE %s WITH OWNER %s"' % (name, user), user='postgres')
    
