"""Test stub: answers every GET with the X-Jarvis-From header it received."""
import http.server
import json


class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        b = json.dumps({'path': self.path, 'from': self.headers.get('X-Jarvis-From')}).encode()
        self.send_response(200)
        self.send_header('content-type', 'application/json')
        self.send_header('content-length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)


http.server.ThreadingHTTPServer(('0.0.0.0', 8000), H).serve_forever()
