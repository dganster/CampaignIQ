"""Load code dependencies before serving; never retain an account report."""
import argparse
import json
import os
from pathlib import Path
import runpy
import sys
from time import perf_counter


def preload_dependencies():
    # report_module deliberately returns a private, unregistered module. Discard
    # it here: only normal imported Python dependencies stay in sys.modules.
    # No render, user, snapshot, original document or database operation occurs.
    from campaigniq.ui.account_dashboard_view import report_module
    report_module()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, required=True)
    parser.add_argument('--check-only', action='store_true')
    args = parser.parse_args()
    root = args.repo
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError('Canonical verified repository directory required')
    from campaigniq.account_deployment_startup import read_release
    from campaigniq.account_release_config import verify_source
    config = read_release(os.environ['CAMPAIGNIQ_ACCOUNT_RELEASE_FILE'],
                          reviewed_sha256=os.environ['CAMPAIGNIQ_ACCOUNT_RELEASE_SHA256'])
    verify_source(config, root)
    port = os.environ.get('PORT', '8501')
    if not port.isdecimal() or not 1 <= int(port) <= 65535:
        raise ValueError('Invalid server port')
    started = perf_counter()
    preload_dependencies()
    print(json.dumps(dict(event='campaigniq.server_boot.v1',
                          dependency_preload_seconds=round(perf_counter()-started,6))), flush=True)
    if args.check_only:
        return
    # Run the existing Streamlit CLI in this process so imported dependencies
    # survive. Streamlit starts listening only after the preload completes.
    sys.argv = ['streamlit', 'run', str(root/'src/campaigniq/ui/account_deployment_entrypoint.py'),
                '--server.address=0.0.0.0', '--server.port='+port, '--server.headless=true']
    runpy.run_module('streamlit', run_name='__main__', alter_sys=True)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        print('Reviewed account server boot failed; private diagnostics required.', file=sys.stderr)
        raise SystemExit(1)
