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

class PidFile:
  pidfile = workdir/'pid'
  def __init__(self, *, pid, port):
    self.pid = pid
    self.port = port

  @staticmethod
  def read():
    if PidFile.pidfile.exists():
      pid, _, port = PidFile.pidfile.read_text().partition(':')
      pidfile = PidFile(pid=int(pid), port=int(port))
      if pidfile.running:
        return pidfile
      else:
        PidFile.pidfile.unlink()

  def write(self):
    if PidFile.pidfile.exists(): raise RuntimeError('PID file already exists')
    PidFile.pidfile.parent.mkdir(parents=True, exist_ok=True)
    PidFile.pidfile.write_text(f"{self.pid}:{self.port}")

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
  _install(server=server, user=user, use_env=use_env, identity_file=identity_file, verify=verify, verbose=verbose)

def _install(*, server, user, use_env, identity_file, verify, verbose):
  # instal sshkube server
  workdir.mkdir(parents=True, exist_ok=True)
  dotenv.set_key(workdir/'.env', 'SSHKUBE_SERVER', server)

  # include sshkube config in sshconfig
  ssh_dir = pathlib.Path('~/.ssh/').expanduser()
  ssh_dir.mkdir(parents=True, exist_ok=True, mode=700)
  add_ssh_config = f"Include {str(workdir/'config')}"
  ssh_config = (ssh_dir/'config').read_text() if (ssh_dir/'config').exists() else ''
  if add_ssh_config not in ssh_config.splitlines():
    ssh_config = add_ssh_config + '\n' + ssh_config
    (ssh_dir/'config').write_text(ssh_config)

  # create sshkube sshconfig
  (workdir/'config').write_text('\n'.join(filter(None, [
    f"Host {server}",
    user and f"    User {user}",
    identity_file and f"    IdentityFile {identity_file}",
    identity_file and f"    IdentitiesOnly yes",
    use_env and f"    ProxyCommand env PYTHONPATH={':'.join(sys.path)} {sys.executable} -m {__package__} openssl -s {server} --verify={verify}",
    (not use_env) and f"    ProxyCommand {sys.executable} -m {__package__} openssl -s {server} --verify={verify}",
  ]))+'\n')

  # verify connection
  try:
    subprocess.check_call(make_ssh_cmd(server=server, flags=['-v'] if verbose else [], cmd=['echo', 'Success!']), stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr)
  except subprocess.CalledProcessError as e:
    raise click.UsageError('Failed to connect, check all options and any above errors') from e


@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def kubeconfig(*, server):
  _kubeconfig(server=server)

def _kubeconfig(*, server):
  ''' get kube config from remote
  '''
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
    if force:
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
    PidFile(pid=proc.pid, port=port).write()
  except RuntimeError as e:
    proc.kill()
    raise click.UsageError('Proxy server failed to start..') from e
  else:
    kubeconfig_['clusters'][0]['cluster']['server'] = f"https://127.0.0.1:{port}"
    with (workdir/'proxy.kube.config').open('w') as fw:
      yaml.safe_dump(kubeconfig_, fw)

@cli.command()
def kill_server():
  _kill_server()

def _kill_server():
  ''' Inspired by adb kill-server
  '''
  pid = PidFile.read()
  if pid: pid.kill()
  (workdir/'kube.config').unlink(missing_ok=True)
  (workdir/'proxy.kube.config').unlink(missing_ok=True)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def init(*, server):
  _init(server=server)

def _init(*, server):
  '''
  Usage: eval "$(sshkube init)"
  '''
  pid = PidFile.read()
  if pid is None:
    _start_server(server=server, force=False)
    pid = PidFile.read()
    assert pid is not None
  #
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
  _run(server=server, args=args)

def _run(*, server, args):
  '''
  Usage: sshkube run kubectl help
  '''
  pid = PidFile.read()
  if pid is None:
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
  _openssl(server=server, verify=verify)

def _openssl(*, server, verify):
  import shutil
  socat = shutil.which('socat')
  if socat:
    subprocess.run([socat, '-', f"openssl:{server}:443,verify={verify}"])
  elif sys.platform == 'win32':
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
