"""
Legacy plugin base classes — removed.

All plugins now use the decorator-based API in opensquad.plugin_api:

    from opensquad.plugin_api import register, tool, hook, on_event, Plugin, Context

    @register("my_plugin", "Author", "Description", "1.0.0")
    class MyPlugin(Plugin):
        ...
"""
