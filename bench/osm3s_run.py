"""Run osm3s_query, optionally measuring its peak resident memory (RSS).

Python starts children with vfork or fork, and Linux carries the parent's peak RSS over into the
child's ru_maxrss at exec. A query's ru_maxrss from os.wait4 therefore includes the Python
process's own memory, which can exceed a small query's. GNU time forks from a process of about
2 MB, so its %M is the query's own peak. With rss=True, the query runs under /usr/bin/time.
"""

import os
import shutil
import signal
import subprocess
import tempfile

GNU_TIME = '/usr/bin/time'


def check_rss_support():
    """Exit with a hint if GNU time isn't installed."""
    if not (os.path.exists(GNU_TIME) and shutil.which(GNU_TIME)):
        raise SystemExit(f'--rss needs GNU time at {GNU_TIME} (e.g. apt-get install time)')


def run_osm3s(bindir, db, query, timeout=None, rss=False):
    """Run one query (bytes). Return (returncode, stdout, stderr, peak_rss_kb or None).

    Raises subprocess.TimeoutExpired after killing the whole process group."""
    cmd = [os.path.join(bindir, 'osm3s_query'), '--db-dir=' + db.rstrip('/') + '/']
    rss_file = None
    if rss:
        fd, rss_file = tempfile.mkstemp(prefix='osm3s-rss-')
        os.close(fd)
        cmd = [GNU_TIME, '-f', '%M', '-o', rss_file] + cmd
    try:
        # A new session, so a timeout kills osm3s_query too, not only GNU time.
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        try:
            out, err = proc.communicate(query, timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()
            raise
        peak = None
        if rss_file:
            with open(rss_file) as f:
                # GNU time writes "Command exited with non-zero status N" first if it failed.
                lines = [line for line in f.read().split('\n') if line.strip()]
            peak = int(lines[-1]) if lines and lines[-1].strip().isdigit() else None
        return proc.returncode, out, err, peak
    finally:
        if rss_file:
            os.unlink(rss_file)
