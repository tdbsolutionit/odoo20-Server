import os
import sys
import traceback
import logging

log_file = r'C:\Work\odoo20-Server\server_debug.log'

def log(msg):
    with open(log_file, 'a', encoding='utf-8') as f:
        f.write(msg + '\n')
    print(msg)

log("Starting Odoo wrapper...")

cur_dir = os.path.dirname(os.path.abspath(__file__))
if cur_dir not in sys.path:
    sys.path.insert(0, cur_dir)

try:
    import odoo.cli
    log("Calling odoo.cli.main with sys.argv: " + str(sys.argv))
    odoo.cli.main()
except SystemExit as se:
    log(f"Odoo exited with SystemExit: code={se.code}")
    traceback.print_exc(file=open(log_file, 'a', encoding='utf-8'))
except Exception as e:
    log(f"Odoo crashed with Exception: {type(e).__name__}: {e}")
    traceback.print_exc(file=open(log_file, 'a', encoding='utf-8'))
except BaseException as be:
    log(f"Odoo crashed with BaseException: {type(be).__name__}: {be}")
    traceback.print_exc(file=open(log_file, 'a', encoding='utf-8'))
finally:
    log("Odoo wrapper finished.")
