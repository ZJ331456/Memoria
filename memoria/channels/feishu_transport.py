"""Official blocking WebSocket SDK isolated in an owned child process."""
from __future__ import annotations
import json
import logging
import threading
from pathlib import Path
from ..private_files import load_private_json
from .feishu_state import FeishuState, digest, parse_event


def listen(credentials_file:str,state_file:str,status_queue,stop):
    # The SDK can log credential-bearing WS URLs and raw events at debug level.
    # No SDK output is used for application logging or IPC.
    logging.disable(logging.CRITICAL)
    import lark_oapi as lark
    private=load_private_json(Path(credentials_file))
    app_id=private['app_id'];app=digest(app_id)
    state=FeishuState(Path(state_file))
    def received(data):
        packet=parse_event(json.loads(lark.JSON.marshal(data)),app_id)
        if packet:state.ingest(app,packet)  # Durable before the SDK acknowledges.
    handler=lark.EventDispatcherHandler.builder('','').register_p2_im_message_receive_v1(received).build()
    client=lark.ws.Client(app_id,private['app_secret'],event_handler=handler,log_level=lark.LogLevel.ERROR)
    logging.disable(logging.CRITICAL)
    def heartbeat():
        while not stop.wait(1):
            try:status_queue.put_nowait({'connected':client._conn is not None})
            except Exception:pass
    status_queue.cancel_join_thread()
    threading.Thread(target=heartbeat,daemon=True).start()
    try:client.start()
    except BaseException:
        try:status_queue.put_nowait({'connected':False,'error':'transport_stopped'})
        except Exception:pass
    finally:state.close()
