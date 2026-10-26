"""极简 WebSocket + CDP 客户端（纯标准库，无第三方依赖）。

用途：连到 Chromium / WebView2 的 --remote-debugging-port，直接在页面里执行 JS，
用于无鼠标、无键盘注入的界面验收。

用法：
    python cdp.py "JS 表达式"            # 在第一个页面里求值并打印结果
    python cdp.py --list                # 列出可调试目标
"""
import base64
import json
import os
import socket
import struct
import sys
import urllib.request


class WS:
    def __init__(self, url):
        if not url.startswith("ws://"):
            raise ValueError("only ws:// supported")
        rest = url[5:]
        hostport, _, path = rest.partition("/")
        path = "/" + path
        host, _, port = hostport.partition(":")
        port = int(port or 80)
        self.sock = socket.create_connection((host, port), timeout=15)

        key = base64.b64encode(os.urandom(16)).decode()
        req = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            "Sec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\n\r\n" % (path, hostport, key)
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise RuntimeError("handshake closed")
            buf += chunk
        head, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in head.split(b"\r\n")[0]:
            raise RuntimeError("handshake failed: " + head.decode("utf-8", "replace"))
        self.buf = rest

    def _read(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise RuntimeError("socket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def send(self, text):
        payload = text.encode("utf-8")
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        mask = os.urandom(4)
        header += mask
        self.sock.sendall(bytes(header) + bytes(b ^ mask[i % 4] for i, b in enumerate(payload)))

    def recv(self, timeout=20):
        self.sock.settimeout(timeout)
        while True:
            b1, b2 = self._read(2)
            opcode = b1 & 0x0F
            masked = b2 & 0x80
            ln = b2 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else None
            data = self._read(ln) if ln else b""
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if opcode == 0x9:
                continue
            if opcode == 0x8:
                raise RuntimeError("closed by peer")
            if opcode in (0x0, 0x1):
                return data.decode("utf-8", "replace")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass


def targets(port=9222):
    # 显式绕过系统/环境代理：本机常设 http_proxy，会把 127.0.0.1 也转发出去，
    # 表现为 502 Bad Gateway 而不是连接失败，很容易误判成「端口没监听」
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open("http://127.0.0.1:%d/json" % port, timeout=8) as r:
        return json.loads(r.read().decode("utf-8"))


def page_ws(port=9222):
    for t in targets(port):
        if t.get("type") == "page" and t.get("webSocketDebuggerUrl"):
            return t["webSocketDebuggerUrl"], t
    raise RuntimeError("no page target")


class CDP:
    def __init__(self, port=9222):
        url, info = page_ws(port)
        self.info = info
        self.ws = WS(url)
        self._id = 0

    def call(self, method, params=None, timeout=20):
        self._id += 1
        mid = self._id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            raw = self.ws.recv(timeout)
            msg = json.loads(raw)
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(str(msg["error"]))
                return msg.get("result", {})

    def eval(self, expr, by_value=True, await_promise=False):
        res = self.call("Runtime.evaluate", {
            "expression": expr,
            "returnByValue": by_value,
            "awaitPromise": await_promise,
            "userGesture": True,
        })
        if res.get("exceptionDetails"):
            d = res["exceptionDetails"]
            raise RuntimeError("JS 异常: " + json.dumps(d.get("exception", d), ensure_ascii=False)[:400])
        return res.get("result", {}).get("value")

    def close(self):
        self.ws.close()


if __name__ == "__main__":
    args = sys.argv[1:]
    if not args or args[0] == "--list":
        for t in targets():
            print(t.get("type"), "|", t.get("title"), "|", t.get("url"))
        sys.exit(0)

    cdp = CDP()
    out = cdp.eval(args[0])
    if isinstance(out, (dict, list)):
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(out)
    cdp.close()
