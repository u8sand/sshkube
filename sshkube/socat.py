''' This is a python implementation of the features of the unix `socat` CLI
`cat` is what you'd get with `-` on the socat cli
`openssl` is what you'd get with `openssl:servername:port,verify`

Thus together you'd use:
await socat.socat(cat(), openssl(host,port))
'''
import sys
import asyncio

async def cat():
  loop = asyncio.get_event_loop()
  reader = asyncio.StreamReader()
  protocol = asyncio.StreamReaderProtocol(reader)
  await loop.connect_read_pipe(lambda: protocol, sys.stdin)
  w_transport, w_protocol = await loop.connect_write_pipe(asyncio.streams.FlowControlMixin, sys.stdout)
  writer = asyncio.StreamWriter(w_transport, w_protocol, reader, loop)
  return reader, writer

async def openssl(*, host: str, port: int, verify=True):
  import ssl
  context = ssl.create_default_context() if verify else ssl._create_unverified_context()
  reader, writer = await asyncio.open_connection(host, port, ssl=context, server_hostname=host)
  return reader, writer

async def pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, b=8192):
  try:
    while True:
      msg = await reader.read(b)
      if not msg: break
      writer.write(msg)
  finally:
    writer.close()

async def socat(left, right, b=8192, u=False, U=False):
  left_reader, left_writer = await left
  right_reader, right_writer = await right
  async with asyncio.TaskGroup() as tg:
    if not U: tg.create_task(pipe(left_reader, right_writer, b=b))
    if not u: tg.create_task(pipe(right_reader, left_writer, b=b))
