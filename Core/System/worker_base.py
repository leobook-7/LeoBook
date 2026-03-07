# worker_base.py: Abstract base class for all LeoBook workers.
# Part of LeoBook Core — System
#
# Functions: execute(), on_failure()
# Called by: supervisor.py | individual worker implementations

from abc import ABC, abstractmethod
import logging

logger = logging.getLogger(__name__)

class BaseWorker(ABC):
    """
    Abstract base class for all pipeline workers.
    Ensures a consistent interface for the Supervisor to dispatch tasks.
    """
    
    def __init__(self, name: str = None):
        self.name = name or self.__class__.__name__

    @abstractmethod
    async def execute(self, *args, **kwargs) -> bool:
        """
        The main execution logic for the worker.
        Returns:
            bool: True if successful, False otherwise.
        """
        pass

    async def on_failure(self, error: Exception):
        """
        Default error handling for worker failures.
        Can be overridden by subclasses for specific cleanup or reporting.
        """
        logger.error(f"Worker {self.name} failed: {error}")
