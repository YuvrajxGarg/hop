"""pywebview walks every public attribute of the js_api object to build window.pywebview.api,
recursing into objects. A public attribute holding the window sent it into the WinForms form's
endless .NET object graph on Windows and froze the app on its splash. Keep only methods public."""

import ast
import pathlib
import unittest

WEBAPP = pathlib.Path(__file__).resolve().parent.parent / "hop" / "webapp.py"


class JsApiTest(unittest.TestCase):
    def test_api_has_no_public_attributes(self):
        tree = ast.parse(WEBAPP.read_text(encoding="utf-8"))
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Api")
        public = sorted({
            n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and isinstance(n.ctx, ast.Store) and isinstance(n.value, ast.Name)
            and ((n.value.id == "self" and cls.lineno <= n.lineno <= cls.end_lineno) or n.value.id == "api")
            and not n.attr.startswith("_")
        })
        self.assertEqual(public, [], "make these _private so pywebview doesn't walk them")


if __name__ == "__main__":
    unittest.main()
