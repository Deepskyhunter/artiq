"""
Core ARTIQ extensions to the Python language.
"""

import linecache, re
from collections import namedtuple
from functools import wraps



__all__ = ["int64", "round64",
           "kernel", "portable", "syscall",
           "set_time_manager", "set_watchdog_factory",
           "ARTIQException"]

# global namespace for kernels
kernel_globals = (
    "sequential", "parallel",
    "delay_mu", "now_mu", "at_mu", "delay",
    "seconds_to_mu", "mu_to_seconds",
    "watchdog"
)
__all__.extend(kernel_globals)


class int64(int):
    """64-bit integers for static compilation.

    When this class is used instead of Python's ``int``, the static compiler
    stores the corresponding variable on 64 bits instead of 32.

    When used in the interpreter, it behaves as ``int`` and the results of
    integer operations involving it are also ``int64`` (which matches the
    size promotion rules of the static compiler). This way, it is possible to
    specify 64-bit size annotations on constants that are passed to the
    kernels.

    Example:

    >>> a = int64(1)
    >>> b = int64(3) + 2
    >>> isinstance(a, int64)
    True
    >>> isinstance(b, int64)
    True
    >>> a + b
    6
    """
    pass

def _make_int64_op_method(int_method):
    def method(self, *args):
        r = int_method(self, *args)
        if isinstance(r, int):
            r = int64(r)
        return r
    return method

for _op_name in ("neg", "pos", "abs", "invert", "round",
                 "add", "radd", "sub", "rsub", "mul", "rmul", "pow", "rpow",
                 "lshift", "rlshift", "rshift", "rrshift",
                 "and", "rand", "xor", "rxor", "or", "ror",
                 "floordiv", "rfloordiv", "mod", "rmod"):
    _method_name = "__" + _op_name + "__"
    _orig_method = getattr(int, _method_name)
    setattr(int64, _method_name, _make_int64_op_method(_orig_method))

for _op_name in ("add", "sub", "mul", "floordiv", "mod",
                 "pow", "lshift", "rshift", "lshift",
                 "and", "xor", "or"):
    _op_method = getattr(int, "__" + _op_name + "__")
    setattr(int64, "__i" + _op_name + "__", _make_int64_op_method(_op_method))


def round64(x):
    """Rounds to a 64-bit integer.

    This function is equivalent to ``int64(round(x))`` but, when targeting
    static compilation, prevents overflow when the rounded value is too large
    to fit in a 32-bit integer.
    """
    return int64(round(x))


_ARTIQEmbeddedInfo = namedtuple("_ARTIQEmbeddedInfo",
                                "core_name function syscall")

def kernel(arg):
    """
    This decorator marks an object's method for execution on the core
    device.

    When a decorated method is called from the Python interpreter, the ``core``
    attribute of the object is retrieved and used as core device driver. The
    core device driver will typically compile, transfer and run the method
    (kernel) on the device.

    When kernels call another method:
        - if the method is a kernel for the same core device, is it compiled
          and sent in the same binary. Calls between kernels happen entirely on
          the device.
        - if the method is a regular Python method (not a kernel), it generates
          a remote procedure call (RPC) for execution on the host.

    The decorator takes an optional parameter that defaults to ``core`` and
    specifies the name of the attribute to use as core device driver.
    """
    if isinstance(arg, str):
        def inner_decorator(function):
            @wraps(function)
            def run_on_core(self, *k_args, **k_kwargs):
                return getattr(self, arg).run(function, ((self,) + k_args), k_kwargs)
            run_on_core.artiq_embedded = _ARTIQEmbeddedInfo(
                core_name=arg, function=function, syscall=None)
            return run_on_core
        return inner_decorator
    else:
        return kernel("core")(arg)

def portable(function):
    """
    This decorator marks a function for execution on the same device as its
    caller.

    In other words, a decorated function called from the interpreter on the
    host will be executed on the host (no compilation and execution on the
    core device). A decorated function called from a kernel will be executed
    on the core device (no RPC).
    """
    function.artiq_embedded = \
        _ARTIQEmbeddedInfo(core_name=None, function=function, syscall=None)
    return function

def syscall(arg):
    """
    This decorator marks a function as a system call. When executed on a core
    device, a C function with the provided name (or the same name as
    the Python function, if not provided) will be called. When executed on
    host, the Python function will be called as usual.

    Every argument and the return value must be annotated with ARTIQ types.

    Only drivers should normally define syscalls.
    """
    if isinstance(arg, str):
        def inner_decorator(function):
            function.artiq_embedded = \
                _ARTIQEmbeddedInfo(core_name=None, function=None,
                                   syscall=function.__name__)
            return function
        return inner_decorator
    else:
        return syscall(arg.__name__)(arg)


class _DummyTimeManager:
    def _not_implemented(self, *args, **kwargs):
        raise NotImplementedError(
            "Attempted to interpret kernel without a time manager")

    enter_sequential = _not_implemented
    enter_parallel = _not_implemented
    exit = _not_implemented
    take_time_mu = _not_implemented
    get_time_mu = _not_implemented
    set_time_mu = _not_implemented
    take_time = _not_implemented

_time_manager = _DummyTimeManager()


def set_time_manager(time_manager):
    """Set the time manager used for simulating kernels by running them
    directly inside the Python interpreter. The time manager responds to the
    entering and leaving of parallel/sequential blocks, delays, etc. and
    provides a time-stamped logging facility for events.
    """
    global _time_manager
    _time_manager = time_manager


class _Sequential:
    """In a sequential block, statements are executed one after another, with
    the time increasing as one moves down the statement list."""
    def __enter__(self):
        _time_manager.enter_sequential()

    def __exit__(self, type, value, traceback):
        _time_manager.exit()
sequential = _Sequential()


class _Parallel:
    """In a parallel block, all top-level statements start their execution at
    the same time.

    The execution time of a parallel block is the execution time of its longest
    statement. A parallel block may contain sequential blocks, which themselves
    may contain parallel blocks, etc.
    """
    def __enter__(self):
        _time_manager.enter_parallel()

    def __exit__(self, type, value, traceback):
        _time_manager.exit()
parallel = _Parallel()


def delay_mu(duration):
    """Increases the RTIO time by the given amount (in machine units)."""
    _time_manager.take_time_mu(duration)


def now_mu():
    """Retrieves the current RTIO time, in machine units."""
    return _time_manager.get_time_mu()


def at_mu(time):
    """Sets the RTIO time to the specified absolute value, in machine units."""
    _time_manager.set_time_mu(time)


def delay(duration):
    """Increases the RTIO time by the given amount (in seconds)."""
    _time_manager.take_time(duration)


def seconds_to_mu(seconds, core=None):
    """Converts seconds to the corresponding number of machine units
    (RTIO cycles).

    :param seconds: time (in seconds) to convert.
    :param core: core device for which to perform the conversion. Specify only
        when running in the interpreter (not in kernel).
    """
    if core is None:
        raise ValueError("Core device must be specified for time conversion")
    return round64(seconds//core.ref_period)


def mu_to_seconds(mu, core=None):
    """Converts machine units (RTIO cycles) to seconds.

    :param mu: cycle count to convert.
    :param core: core device for which to perform the conversion. Specify only
        when running in the interpreter (not in kernel).
    """
    if core is None:
        raise ValueError("Core device must be specified for time conversion")
    return mu*core.ref_period


class _DummyWatchdog:
    def __init__(self, timeout):
        pass

    def __enter__(self):
        pass

    def __exit__(self, type, value, traceback):
        pass


# Watchdogs are simply not enforced by default.
_watchdog_factory = _DummyWatchdog


def set_watchdog_factory(f):
    global _watchdog_factory
    _watchdog_factory = f


def watchdog(timeout):
    return _watchdog_factory(timeout)


class ARTIQException(Exception):
    """Base class for exceptions raised or passed through the core device."""

    # Try and create an instance of the specific class, if one exists.
    def __new__(cls, name, message, params, traceback):
        def find_subclass(cls):
            if cls.__name__ == name:
                return cls
            else:
                for subclass in cls.__subclasses__():
                    cls = find_subclass(subclass)
                    if cls is not None:
                        return cls

        more_specific_cls = find_subclass(cls)
        if more_specific_cls is None:
            more_specific_cls = cls

        exn = Exception.__new__(more_specific_cls)
        exn.__init__(name, message, params, traceback)
        return exn

    def __init__(self, name, message, params, traceback):
        Exception.__init__(self, name, message, *params)
        self.name, self.message, self.params = name, message, params
        self.traceback = list(traceback)

    def __str__(self):
        lines = []

        if type(self).__name__ == self.name:
            lines.append(self.message.format(*self.params))
        else:
            lines.append("({}) {}".format(self.name, self.message.format(*self.params)))

        lines.append("Core Device Traceback (most recent call last):")
        for (filename, line, column, function, address) in self.traceback:
            source_line = linecache.getline(filename, line)
            indentation = re.search(r"^\s*", source_line).end()

            if address is None:
                formatted_address = ""
            else:
                formatted_address = " (RA=0x{:x})".format(address)

            if column == -1:
                lines.append("  File \"{file}\", line {line}, in {function}{address}".
                             format(file=filename, line=line, function=function,
                                    address=formatted_address))
                lines.append("    {}".format(source_line.strip() if source_line else "<unknown>"))
            else:
                lines.append("  File \"{file}\", line {line}, column {column},"
                             " in {function}{address}".
                             format(file=filename, line=line, column=column + 1,
                                    function=function, address=formatted_address))
                lines.append("    {}".format(source_line.strip() if source_line else "<unknown>"))
                lines.append("    {}^".format(" " * (column - indentation)))

        return "\n".join(lines)
