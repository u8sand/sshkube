''' Usage:
# saves this server for future use
sshkube -s sshkube.dev.maayanlab.cloud install

# launches socks5 proxy and configures kubectl
$(sshkube init)
# now you can use kubectl/helm
kubectl get nodes
helm list

# alternatively you can do
sshkube run kubectl get nodes
sshkube run helm list
'''
import os
import re
import sys
import yaml
import click
import pathlib
import subprocess
from urllib.parse import urlparse
import dotenv

dotenv.load_dotenv()
workdir = pathlib.Path(os.environ.get('SSHKUBE_CONFIG', '~/.sshkube/')).expanduser()
dotenv.load_dotenv(workdir/'.env')

def Popen(*args, **kwargs):
  ''' Fixup some platform-specific oddities
  '''
  if sys.platform == 'win32':
    if kwargs.get('start_new_session'):
      kwargs['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
  return subprocess.Popen(*args, **kwargs)

@click.group()
@click.version_option()
def cli(): pass

def get_free_port():
  import socket
  sock = socket.socket()
  sock.bind(('', 0))
  _, port = sock.getsockname()
  sock.close()
  return port

def wait_for_port(port, timeout=1, backoff=1, retries=3):
  import time, socket
  for i in range(retries):
    if i > 0: time.sleep(backoff)
    try:
      sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
      sock.settimeout(timeout)
      result = sock.connect_ex(('127.0.0.1', port))
      if result == 0: return
    except socket.error:
      pass
    finally:
      sock.close()
  raise RuntimeError(f"Proxy server didn't start!")

def kubectl_livez(port, timeout=1):
  import ssl, urllib.request, urllib.error
  try:
    with urllib.request.urlopen(f"https://127.0.0.1:{port}/livez", timeout=timeout, context=ssl._create_unverified_context()) as res:
      return res.getcode()
  except urllib.error.HTTPError as e:
    return e.getcode()
  except:
    return 600

class PidFile:
  pidfile = workdir/'pid'
  def __init__(self, *, netloc, pid, port):
    self.netloc = netloc
    self.pid = pid
    self.port = port

  @staticmethod
  def read():
    if PidFile.pidfile.exists():
      pid, _, netloc_port = PidFile.pidfile.read_text().partition(':')
      netloc, _, port = netloc_port.partition(':')
      if not _:
        port = netloc
        netloc = ''
      pidfile = PidFile(netloc=netloc, pid=int(pid), port=int(port))
      if pidfile.running:
        return pidfile
      else:
        PidFile.pidfile.unlink()

  def write(self):
    if PidFile.pidfile.exists(): raise RuntimeError('PID file already exists')
    PidFile.pidfile.parent.mkdir(parents=True, exist_ok=True)
    PidFile.pidfile.write_text(f"{self.pid}:{self.netloc}:{self.port}")

  @property
  def running(self):
    import os
    try: os.kill(self.pid, 0)
    except OSError: return False
    else: return True

  def kill(self):
    import os, signal
    os.kill(self.pid, signal.SIGINT)
    PidFile.pidfile.unlink()

class SSHConfigFile:
  file = workdir/'config'

  @staticmethod
  def init():
    # include sshkube config in sshconfig
    ssh_dir = pathlib.Path('~/.ssh/').expanduser()
    ssh_dir.mkdir(parents=True, exist_ok=True, mode=700)
    add_ssh_config = f"Include {str(workdir/'config')}"
    ssh_config = (ssh_dir/'config').read_text() if (ssh_dir/'config').exists() else ''
    if add_ssh_config not in ssh_config.splitlines():
      ssh_config = add_ssh_config + '\n' + ssh_config
      (ssh_dir/'config').write_text(ssh_config)

  @staticmethod
  def read():
    SSHConfigFile.file.parent.mkdir(parents=True, exist_ok=True)
    current_config = SSHConfigFile.file.read_text() if SSHConfigFile.file.exists() else ''
    return {
      m.group(1): m.group(0)
      for m in re.finditer(r'Host (.+?)(\n +.+)+', current_config)
    }

  @staticmethod
  def hosts():
    return SSHConfigFile.read().keys()
  
  @staticmethod
  def install(*, server, user, identity_file, use_env, verify):
    SSHConfigFile.init()
    hosts = SSHConfigFile.read()
    hosts[server] = '\n'.join(filter(None, [
      f"Host {server}",
      user and f"    User {user}",
      f"    IdentitiesOnly yes",
      identity_file and f"    IdentityFile {identity_file}",
      use_env and f"    ProxyCommand env \"PYTHONPATH={':'.join(sys.path)}\" \"{sys.executable}\" -m {__package__} openssl -s {server} --verify={verify}",
      (not use_env) and f"    ProxyCommand \"{sys.executable}\" -m {__package__} openssl -s {server} --verify={verify}",
    ]))
    SSHConfigFile.file.write_text('\n\n'.join(hosts.values()))

  @staticmethod
  def uninstall(*, server):
    hosts = SSHConfigFile.read()
    del hosts[server]
    SSHConfigFile.file.write_text('\n\n'.join(hosts.values()))

def make_ssh_cmd(*, server, cmd=[], flags=[]):
  return ['ssh', *flags, server, *cmd]

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
@click.option('-u', '--user', type=str, default='')
@click.option('-i', '--identity-file', type=str, default='')
@click.option('--verify', type=int, default=1)
@click.option('-e', '--use-env', type=bool, is_flag=True, default=False)
@click.option('-v', '--verbose', type=bool, is_flag=True, default=False)
def install(*, server, user, use_env, identity_file, verify, verbose):
  ''' Install a new server to use with sshkube
  '''
  _install(server=server, user=user, use_env=use_env, identity_file=identity_file, verify=verify, verbose=verbose)

def _install(*, server, user, use_env, identity_file, verify, verbose):
  # update sshkube sshconfig
  SSHConfigFile.install(server=server, user=user, identity_file=identity_file, use_env=use_env, verify=verify)

  # verify connection
  try:
    subprocess.check_call(make_ssh_cmd(server=server, flags=['-v'] if verbose else [], cmd=['echo', 'Success!']), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
  except subprocess.CalledProcessError as e:
    raise click.UsageError('Failed to connect, check all options and any above errors') from e
  
  _use(server=server)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def list(*, server):
  ''' List currently installed servers
  '''
  _list(server=server)

def _list(*, server):
  print('\n'.join(['Servers (* is in use):']+[
    (' *  ' if server == host else '    ') + host
    for host in SSHConfigFile.hosts()
  ]), file=sys.stderr)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def use(*, server):
  ''' Use a specific configured server
  '''
  _use(server=server)

def _use(*, server):
  dotenv.set_key(workdir/'.env', 'SSHKUBE_SERVER', server)
  _list(server=server)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def uninstall(*, server):
  ''' Uninstall a previously installed server
  '''
  _uninstall(server=server)

def _uninstall(*, server):
  _kill_server()
  SSHConfigFile.uninstall(server=server)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def kubeconfig(*, server):
  ''' [internal]: Obtain kubeconfig from remote
  '''
  _kubeconfig(server=server)

def _kubeconfig(*, server):
  try:
    kube_config = subprocess.check_output(make_ssh_cmd(server=server, cmd=['cat', '~/.kube/config']))
  except subprocess.CalledProcessError:
    raise click.UsageError('Failed to get kubeconfig')
  else:
    (workdir/'kube.config').write_bytes(kube_config)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
@click.option('-f', '--force', type=bool, is_flag=True)
def start_server(*, server, force):
  ''' Inspired by adb start-server
  '''
  _start_server(server=server, force=force)

def _start_server(*, server, force):
  pid = PidFile.read()
  if pid:
    if force or pid.netloc != server:
      _kill_server()
    elif kubectl_livez(pid.port) >= 500:
      # permission denied error is also fine if connection is broken we get a 600
      _kill_server()
    else:
      return
  #
  try:
    _kubeconfig(server=server)
  except RuntimeError as e:
    proc.kill()
    raise click.UsageError('Failed to get kubeconfig from server..') from e
  #
  with (workdir/'kube.config').open('r') as fr:
    kubeconfig_ = yaml.safe_load(fr)
  k8s_server = kubeconfig_['clusters'][0]['cluster']['server']
  k8s_server_parsed = urlparse(k8s_server)
  port = get_free_port()
  proc = Popen(make_ssh_cmd(server=server, flags=[f"-NL{port}:{k8s_server_parsed.netloc}"]), start_new_session=True)
  try:
    wait_for_port(port)
    if kubectl_livez(port) >= 500:
      raise click.UsageError('Kubernetes not available')
    PidFile(netloc=server, pid=proc.pid, port=port).write()
  except RuntimeError as e:
    proc.kill()
    raise click.UsageError('Proxy server failed to start..') from e
  else:
    kubeconfig_['clusters'][0]['cluster']['server'] = f"https://127.0.0.1:{port}"
    with (workdir/'proxy.kube.config').open('w') as fw:
      yaml.safe_dump(kubeconfig_, fw)

@cli.command()
def kill_server():
  ''' [internal]: Explicitly kill the proxy server when something went wrong

  Inspired by adb kill-server
  '''
  _kill_server()

def _kill_server():
  pid = PidFile.read()
  if pid: pid.kill()
  (workdir/'kube.config').unlink(missing_ok=True)
  (workdir/'proxy.kube.config').unlink(missing_ok=True)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def init(*, server):
  ''' Configure current shell to access the sshkube kubeconfig

  Usage: eval "$(sshkube init)"
  '''
  _init(server=server)

def _init(*, server):
  _start_server(server=server, force=False)
  _list(server=server)
  pid = PidFile.read()
  assert pid is not None
  #
  if sys.platform == 'win32':
    print(
      f"set KUBECONFIG={workdir/'proxy.kube.config'}",
      sep='\n',
    )
  else:
    print(
      f"export KUBECONFIG={workdir/'proxy.kube.config'}",
      sep='\n',
    )

@cli.command(context_settings=dict(
  ignore_unknown_options=True,
  allow_interspersed_args=False,
))
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
def run(*, server, args):
  ''' Run a command ensuring that the kubeconfig is set properly

  Usage: sshkube run kubectl help
  '''
  _run(server=server, args=args)

def _run(*, server, args):
  pid = PidFile.read()
  _start_server(server=server, force=False)
  pid = PidFile.read()
  assert pid is not None
  #
  subprocess.run(args, env=dict(
    os.environ,
    KUBECONFIG=f"{workdir/'proxy.kube.config'}"
  ))

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
@click.option('--verify', type=int, default=1)
def openssl(*, server, verify):
  ''' [internal]: proxy stdin <-> ssl
  '''
  _openssl(server=server, verify=verify)

def _openssl(*, server, verify):
  if sys.platform == 'win32':
    import winloop
    winloop.run(_async_openssl(server=server, verify=verify))
  else:
    import asyncio
    asyncio.new_event_loop().run_until_complete(_async_openssl(server=server, verify=verify))

async def _async_openssl(*, server, verify):
  from sshkube import socat
  await socat.socat(socat.cat(), socat.openssl(host=server, port=443, verify=verify))

if __name__ == '__main__':
  cli()
