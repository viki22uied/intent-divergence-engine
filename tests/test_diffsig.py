from intent_ide.diffsig import extract_signatures_from_diff, signatures_prompt_block

DIFF = """diff --git a/shop.py b/shop.py
index 111..222 100644
--- a/shop.py
+++ b/shop.py
@@ -1,4 +1,8 @@
+def cart_total(items, tax=0.0):
+    return sum(i['price'] for i in items)
+
 def existing_helper():
     return 1
+    def _private_nested():
+        pass
diff --git a/other.py b/other.py
--- a/other.py
+++ b/other.py
@@ -0,0 +1,2 @@
+def other_new(x):
+    return x
"""


def test_extracts_added_public_functions():
    fns = extract_signatures_from_diff(DIFF)
    names = {f.name for f in fns}
    assert "cart_total" in names
    assert "other_new" in names


def test_skips_private_and_context():
    fns = extract_signatures_from_diff(DIFF)
    assert all(not f.name.startswith("_") for f in fns)
    assert "existing_helper" not in names_of(fns)


def names_of(fns):
    return {f.name for f in fns}


def test_prompt_block_format():
    fns = extract_signatures_from_diff(DIFF)
    block = signatures_prompt_block(fns)
    assert block.startswith("Functions changed by this diff:")
    assert "shop.py: def cart_total(" in block


def test_empty_diff():
    assert extract_signatures_from_diff("") == []
