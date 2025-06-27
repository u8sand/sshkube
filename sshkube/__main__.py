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
import click
import pathlib
import subprocess
import dotenv

subprocess.CREATE_NEW_PROCESS_GROUP = 0x00000200
subprocess.DETACHED_PROCESS = 0x00000008

workdir = pathlib.Path('~/.sshkube/').expanduser()
dotenv.load_dotenv(workdir/'.env')

@click.group()
def cli(): pass

def get_free_port():
  import socket
  sock = socket.socket()
  sock.bind(('', 0))
  _, port = sock.getsockname()
  sock.close()
  return port

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

def make_ssh_cmd(flags, server, cmd):
  import sys, shutil
  socat = shutil.which('socat')
  return ['ssh', '-o',
          f"ProxyCommand {socat} - openssl:{server}:443,verify=0" if socat is not None else f"ProxyCommand {sys.executable} -m sshkube_client openssl {server}",
          *flags,
          server,
          *cmd]

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def install(server):
  _install(server=server)

def _install(*, server):
  workdir.mkdir(parents=True, exist_ok=True)
  import dotenv; dotenv.set_key(workdir/'.env', 'SSHKUBE_SERVER', server)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def kubeconfig(server):
  _kubeconfig(server=server)

def _kubeconfig(*, server):
  ''' get kube config from remote
  '''
  try:
    kube_config = subprocess.check_output(make_ssh_cmd([], server, ['cat', '~/.kube/config']))
  except subprocess.CalledProcessError:
    raise RuntimeError('Failed to get kubeconfig')
  else:
    (workdir/'kube.config').write_bytes(kube_config)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
@click.option('-f', '--force', type=bool, is_flag=True)
def start_server(server, force):
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
  port = get_free_port()
  proc = subprocess.Popen(make_ssh_cmd([f"-ND{port}"], server, []), start_new_session=True)
  try:
    PidFile(pid=proc.pid, port=port).write()
  except RuntimeError:
    proc.kill()
    raise
  else:
    try:
      _kubeconfig(server=server)
    except RuntimeError:
      proc.kill()
      raise

@cli.command()
def kill_server():
  _kill_server()

def _kill_server():
  ''' Inspired by adb kill-server
  '''
  pid = PidFile.read()
  if pid: pid.kill()
  (workdir/'kube.config').unlink(missing_ok=True)

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def init(server):
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
    f"export KUBECONFIG={workdir/'kube.config'}",
    f"export HTTPS_PROXY=socks5://127.0.0.1:{pid.port}",
    sep='\n',
  )

@cli.command(context_settings=dict(
  ignore_unknown_options=True,
  allow_interspersed_args=False,
))
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
@click.argument('args', nargs=-1, type=click.UNPROCESSED)
def run(server, args):
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
  import os, shutil
  cmd = shutil.which('env')
  os.execv(cmd, [
    f"KUBECONFIG={workdir/'kube.config'}",
    f"HTTPS_PROXY=socks5://127.0.0.1:{pid.port}",
    *args,
  ])

@cli.command()
@click.option('-s', '--server', envvar='SSHKUBE_SERVER', type=str, required=True)
def openssl(server):
  _openssl(server=server)

def _openssl(*server):
  socat = shutil.which('socat')
  if socat:
    subprocess.run([socat, '-', f"OPENSSL:{server}:443"])
  else:
    # implement socat's functionality in native python
    import os
    import ssl
    import sys
    import shutil
    import select
    import socket
    def set_nonblocking(fd):
      import os, fcntl
      flags = fcntl.fcntl(fd, fcntl.F_GETFL)
      fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    MTU = 4096
    context = ssl.create_default_context()
    with socket.create_connection((server, 443)) as sock:
      with context.wrap_socket(sock, server_hostname=server) as ssock:
        ssock.setblocking(False)
        set_nonblocking(sys.stdin.fileno())
        while True:
          rlist, _, _ = select.select([ssock, sys.stdin], [], [])
          if ssock in rlist:
              data = ssock.recv(MTU)
              if not data:
                  continue
              sys.stdout.buffer.write(data)
              sys.stdout.buffer.flush()
          if sys.stdin in rlist:
              input_data = os.read(sys.stdin.fileno(), MTU)
              if not input_data:
                  continue
              ssock.sendall(input_data)

if __name__ == '__main__':
  cli()
