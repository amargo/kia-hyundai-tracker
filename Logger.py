import logging
import coloredlogs
import os
from dotenv import load_dotenv

class Logger:
    _instance = None
    _initialized = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(Logger, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not Logger._initialized:
            load_dotenv()
            debug_mode = os.getenv('DEBUG', 'false').lower() == 'true'
            
            # Configure root logger
            self.logger = logging.getLogger()
            coloredlogs.install(
                level='DEBUG' if debug_mode else 'INFO',
                logger=self.logger,
                isatty=True,
                fmt='%(asctime)s %(name)s[%(process)d] %(levelname)s %(message)s'
            )
            
            Logger._initialized = True

    @staticmethod
    def get_logger(name=None):
        """Get a logger instance with the specified name"""
        logger_instance = Logger()
        if name:
            return logging.getLogger(name)
        return logger_instance.logger
