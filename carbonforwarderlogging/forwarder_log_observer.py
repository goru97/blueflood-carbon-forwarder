import os

from twisted.logger import FilteringLogObserver, LogLevelFilterPredicate, LogLevel, jsonFileLogObserver
from twisted.python import logfile

log_dir = '.'
log_level = 'INFO'
log_rotate_length = 10000
max_rotated_log_files = 100

def get_log_observer():
    f = logfile.LogFile("carbon_forwarder.log", log_dir, log_rotate_length, max_rotated_log_files)
    observer = jsonFileLogObserver(f)
    filterer = FilteringLogObserver(observer,
        [LogLevelFilterPredicate(
            LogLevel.levelWithName(os.environ.get("TWISTED_LOG_LEVEL", log_level).lower()))])
    return filterer
