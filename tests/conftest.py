import os
import tempfile

# Тесты работают на отдельной временной БД и без ключей, как на чистой машине.
_tmp = tempfile.mkdtemp(prefix="marginguard-test-")
os.environ["MARGINGUARD_DATA_DIR"] = _tmp
os.environ["ANTHROPIC_API_KEY"] = ""
