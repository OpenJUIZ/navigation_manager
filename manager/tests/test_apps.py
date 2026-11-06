import email.parser
import email.policy
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer

from navigation_manager.apps import call_multipart, map_parts


class MultipartTest(unittest.TestCase):
    def test_map_parts_from_mobile_sim_info(self):
        p = map_parts(b"png", {"resolution": 0.05, "origin": [-5, -5, 0], "width": 200, "height": 200})
        self.assertEqual(p["image"], b"png")
        self.assertEqual(p["origin"], [-5, -5, 0])
        self.assertFalse(p["negate"])
        self.assertEqual((p["occupied_thresh"], p["free_thresh"]), (0.65, 0.196))

    def test_call_multipart_sends_named_parts(self):
        got = {}

        class H(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def do_PUT(self):
                body = self.rfile.read(int(self.headers["Content-Length"]))
                msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(
                    b"Content-Type: " + self.headers["Content-Type"].encode() + b"\r\n\r\n" + body)
                for part in msg.iter_parts():
                    got[part.get_param("name", header="content-disposition")] = (
                        part.get_content_type(), part.get_payload(decode=True))
                got["path"] = self.path
                out = b"{}"
                self.send_response(200)
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.handle_request, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_port}"
        call_multipart(base, "amcl0", "set_map0", "amcl_set_map", map_parts(b"\x89PNG", {"resolution": 0.05, "origin": [1, 2]}))
        srv.server_close()
        self.assertIn("set_map0%3A%3Aamcl_set_map%3Aamcl0", got["path"])
        self.assertEqual(got["image"], ("image/png", b"\x89PNG"))
        self.assertEqual(json.loads(got["resolution"][1]), 0.05)
        self.assertEqual(json.loads(got["origin"][1]), [1, 2])
        self.assertEqual(got["negate"][0], "application/json")


if __name__ == "__main__":
    unittest.main()
