class LazyObject(object):
    """
    A wrapper for another class that can be used to delay instantiation of the
    wrapped class.
    """
    def __init__(self, *args, **kwargs):
        self._wrapped = None
        self._wrapped_args = args
        self._wrapped_kwargs = kwargs

    def __getattr__(self, name):
        if self._wrapped is None:
            self._setup()
        return getattr(self._wrapped, name)

    def __setattr__(self, name, value):
        if name in ["_wrapped", "_wrapped_args", "_wrapped_kwargs"]:
            # Assign to __dict__ to avoid infinite __setattr__ loops.
            self.__dict__[name] = value
        else:
            if self._wrapped is None:
                self._setup()
            setattr(self._wrapped, name, value)

    def __delattr__(self, name):
        if name == ["_wrapped", "_wrapped_args", "_wrapped_kwargs"]:
            raise TypeError("can't delete %s." % (name,))
        if self._wrapped is None:
            self._setup()
        delattr(self._wrapped, name)

    def _setup(self):
        """
        Must be implemented by subclasses to initialise the wrapped object.
        """
        raise NotImplementedError

    # introspection support:
    __members__ = property(lambda self: self.__dir__())

    def __dir__(self):
        if self._wrapped is None:
            self._setup()
        return dir(self._wrapped)

    def __call__(self, *args, **kwargs):
        if self._wrapped is None:
            self._setup()
        return self._wrapped(*args, **kwargs)

    def __add__(self, other):
        if self._wrapped is None:
            self._setup()
        return self._wrapped + other
