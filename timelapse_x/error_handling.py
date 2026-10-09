"""
Error Handling Standardization — Timelapse X Addon

Cleanup (v2.5.4):
  - Removed example_usage() function (40+ lines of dead code that created
    real Blender objects; only called under `if __name__ == '__main__'`).
"""

import logging
from typing import Optional, Any, Dict, Callable
from enum import Enum
import traceback

logger = logging.getLogger(__name__)


# ============================================================================
# Error Categories
# ============================================================================

class ErrorCategory(Enum):
    CRITICAL   = "critical"
    RECOVERABLE= "recoverable"
    WARNING    = "warning"
    VALIDATION = "validation"
    RESOURCE   = "resource"
    STATE      = "state"


class ErrorSeverity(Enum):
    CRITICAL = "critical"
    ERROR    = "error"
    WARNING  = "warning"
    INFO     = "info"


# ============================================================================
# Custom Exception Hierarchy
# ============================================================================

class TimelapseXError(Exception):
    def __init__(self, message: str,
                 category:     ErrorCategory = ErrorCategory.CRITICAL,
                 severity:     ErrorSeverity = ErrorSeverity.ERROR,
                 context:      Optional[Dict[str, Any]] = None,
                 user_message: Optional[str] = None,
                 recoverable:  bool = False):
        super().__init__(message)
        self.message      = message
        self.category     = category
        self.severity     = severity
        self.context      = context or {}
        self.user_message = user_message or message
        self.recoverable  = recoverable
        self._log_error()

    def _log_error(self):
        log_msg  = f"[{self.category.value.upper()}] {self.message}"
        if self.context:
            log_msg += f" | Context: {self.context}"
        levels = {
            ErrorSeverity.CRITICAL: logger.critical,
            ErrorSeverity.ERROR:    logger.error,
            ErrorSeverity.WARNING:  logger.warning,
        }
        levels.get(self.severity, logger.info)(log_msg)

    def get_user_message(self) -> str:
        return self.user_message

    def to_dict(self) -> Dict[str, Any]:
        return {
            'message':      self.message,
            'category':     self.category.value,
            'severity':     self.severity.value,
            'context':      self.context,
            'user_message': self.user_message,
            'recoverable':  self.recoverable,
        }


# ============================================================================
# Specific Error Types
# ============================================================================

class CaptureError(TimelapseXError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message,
                         category=ErrorCategory.RECOVERABLE,
                         severity=ErrorSeverity.ERROR,
                         user_message=kwargs.pop('user_message', "Failed to capture frame"),
                         recoverable=True, **kwargs)


class StateError(TimelapseXError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message,
                         category=ErrorCategory.STATE,
                         severity=ErrorSeverity.ERROR,
                         user_message=kwargs.pop('user_message', "Invalid operation state"),
                         recoverable=False, **kwargs)


class ValidationError(TimelapseXError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message,
                         category=ErrorCategory.VALIDATION,
                         severity=ErrorSeverity.WARNING,
                         user_message=kwargs.pop('user_message', "Invalid input"),
                         recoverable=False, **kwargs)


class ResourceError(TimelapseXError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message,
                         category=ErrorCategory.RESOURCE,
                         severity=ErrorSeverity.ERROR,
                         user_message=kwargs.pop('user_message', "Resource unavailable"),
                         recoverable=True, **kwargs)


class ConfigurationError(TimelapseXError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message,
                         category=ErrorCategory.VALIDATION,
                         severity=ErrorSeverity.ERROR,
                         user_message=kwargs.pop('user_message', "Invalid configuration"),
                         recoverable=False, **kwargs)


class RateLimitError(TimelapseXError):
    def __init__(self, message: str, **kwargs):
        super().__init__(message,
                         category=ErrorCategory.RESOURCE,
                         severity=ErrorSeverity.WARNING,
                         user_message=kwargs.pop('user_message', "Too many requests, please slow down"),
                         recoverable=True, **kwargs)


# ============================================================================
# Error Handler Decorator
# ============================================================================

def handle_errors(default_return: Any = None, raise_on_error: bool = True,
                  log_traceback: bool = True,
                  category: ErrorCategory = ErrorCategory.CRITICAL,
                  user_message: Optional[str] = None):
    def decorator(func: Callable):
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except TimelapseXError:
                if raise_on_error:
                    raise
                return default_return
            except Exception as e:
                error_msg = f"{func.__name__} failed: {str(e)}"
                if log_traceback:
                    logger.error(f"{error_msg}\n{traceback.format_exc()}")
                wrapped = TimelapseXError(
                    error_msg, category=category, severity=ErrorSeverity.ERROR,
                    context={'function': func.__name__,
                             'original_exception': str(e)},
                    user_message=user_message or f"Operation failed: {str(e)}",
                    recoverable=False
                )
                if raise_on_error:
                    raise wrapped from e
                return default_return
        return wrapper
    return decorator


# ============================================================================
# Error Context Manager
# ============================================================================

class ErrorContext:
    def __init__(self, operation: str,
                 category:     ErrorCategory = ErrorCategory.CRITICAL,
                 recoverable:  bool = False,
                 on_error:     Optional[Callable] = None,
                 user_message: Optional[str] = None):
        self.operation    = operation
        self.category     = category
        self.recoverable  = recoverable
        self.on_error     = on_error
        self.user_message = user_message
        self.error: Optional[TimelapseXError] = None
        self.success = False

    def __enter__(self):
        logger.debug(f"→ Starting: {self.operation}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        if exc_type is None:
            self.success = True
            logger.debug(f"✓ Completed: {self.operation}")
            return True

        if isinstance(exc_val, TimelapseXError):
            self.error = exc_val
        else:
            self.error = TimelapseXError(
                f"{self.operation} failed: {str(exc_val)}",
                category=self.category, severity=ErrorSeverity.ERROR,
                context={'original_exception': str(exc_val)},
                user_message=self.user_message or f"{self.operation} failed",
                recoverable=self.recoverable
            )

        if self.on_error:
            try:
                self.on_error(self.error)
            except Exception as e:
                logger.error(f"Error handler failed: {e}")

        if self.recoverable:
            logger.warning(f"⚠ Recoverable error in {self.operation}: {self.error.message}")
            return True
        else:
            logger.error(f"✗ Critical error in {self.operation}: {self.error.message}")
            return False


# ============================================================================
# Error Recovery
# ============================================================================

class ErrorRecovery:
    @staticmethod
    def retry(func: Callable, max_attempts: int = 3, delay: float = 0.5,
              backoff: float = 2.0, exceptions: tuple = (TimelapseXError,)) -> Any:
        import time
        attempt       = 0
        current_delay = delay
        last_error    = None

        while attempt < max_attempts:
            attempt += 1
            try:
                logger.debug(f"Attempt {attempt}/{max_attempts}: {func.__name__}")
                return func()
            except exceptions as e:
                last_error = e
                if attempt < max_attempts:
                    logger.warning(f"Attempt {attempt} failed: {str(e)}, "
                                   f"retrying in {current_delay:.1f}s...")
                    time.sleep(current_delay)
                    current_delay *= backoff
                else:
                    logger.error(f"All {max_attempts} attempts failed")

        if last_error:
            raise last_error

    @staticmethod
    def fallback(primary: Callable, fallback: Callable,
                 exceptions: tuple = (TimelapseXError,)) -> Any:
        try:
            logger.debug(f"Trying primary: {primary.__name__}")
            return primary()
        except exceptions as e:
            logger.warning(f"Primary failed: {str(e)}, falling back to {fallback.__name__}")
            return fallback()


# ============================================================================
# Error Reporter
# ============================================================================

class ErrorReporter:
    def __init__(self):
        self.errors    = []
        self.max_errors = 100

    def report(self, error: TimelapseXError):
        import time
        self.errors.append({'timestamp': time.time(), 'error': error.to_dict()})
        if len(self.errors) > self.max_errors:
            self.errors = self.errors[-self.max_errors:]

    def get_recent_errors(self, count: int = 10) -> list:
        return self.errors[-count:]

    def get_errors_by_category(self, category: ErrorCategory) -> list:
        return [e for e in self.errors if e['error']['category'] == category.value]

    def get_error_stats(self) -> dict:
        stats = {'total': len(self.errors), 'by_category': {}, 'by_severity': {}}
        for entry in self.errors:
            error = entry['error']
            cat   = error['category']
            sev   = error['severity']
            stats['by_category'][cat] = stats['by_category'].get(cat, 0) + 1
            stats['by_severity'][sev] = stats['by_severity'].get(sev, 0) + 1
        return stats

    def clear(self):
        self.errors.clear()

    def print_stats(self):
        stats = self.get_error_stats()
        print("\n" + "="*70)
        print("ERROR STATISTICS")
        print("="*70)
        print(f"Total Errors: {stats['total']}")
        if stats['by_category']:
            print("\nBy Category:")
            for cat, count in stats['by_category'].items():
                print(f"  {cat}: {count}")
        if stats['by_severity']:
            print("\nBy Severity:")
            for sev, count in stats['by_severity'].items():
                print(f"  {sev}: {count}")
        print("="*70 + "\n")


_error_reporter = ErrorReporter()


def get_error_reporter() -> ErrorReporter:
    return _error_reporter


# ============================================================================
# Utility Functions
# ============================================================================

def report_error(error: TimelapseXError):
    _error_reporter.report(error)


def get_error_stats() -> dict:
    return _error_reporter.get_error_stats()


def clear_errors():
    _error_reporter.clear()


def show_error_to_user(error: TimelapseXError, context=None):
    try:
        logger.info(f"User message: {error.get_user_message()}")
    except Exception as e:
        logger.error(f"Failed to show error to user: {e}")


# ============================================================================
# Registration
# ============================================================================

def register():
    logger.info("Error handling registered (v2.5.4: removed dead example_usage)")


def unregister():
    logger.info("Unregistering error handling")
    reporter = get_error_reporter()
    stats    = reporter.get_error_stats()
    if stats['total'] > 0:
        logger.info(f"Total errors during session: {stats['total']}")
        reporter.print_stats()
    reporter.clear()
