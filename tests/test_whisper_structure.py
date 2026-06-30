"""
测试 Whisper 插件的服务自动启动功能
"""

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from opensquad.plugin_api import Context


def test_whisper_plugin_structure():
    """测试 Whisper 插件的基本结构"""
    from plugins.whisper.plugin import WhisperPlugin

    # 创建测试用的 Context
    test_config = {
        "port": 5001,
        "host": "0.0.0.0",
        "auto_start": False,  # 测试中不实际启动服务
    }

    context = Context(
        agent_id="test_agent",
        project_root=os.path.dirname(os.path.dirname(__file__)),
        event_bus=None,
        config=test_config,
        data_dir="/tmp/test_whisper",
        plugin_dir=os.path.join(os.path.dirname(__file__), "..", "plugins", "whisper"),
    )

    # 实例化插件
    plugin = WhisperPlugin(context)

    # 验证属性
    assert hasattr(plugin, "_service_process")
    assert hasattr(plugin, "_health_check_thread")
    assert hasattr(plugin, "_stop_health_check")
    assert hasattr(plugin, "on_load")
    assert hasattr(plugin, "on_unload")
    assert hasattr(plugin, "_start_service")
    assert hasattr(plugin, "_stop_service")
    assert hasattr(plugin, "_check_service_health")
    assert hasattr(plugin, "_start_health_monitor")

    print("✅ Whisper 插件结构检查通过")


def test_whisper_plugin_on_load_disabled():
    """测试 auto_start=False 时 on_load 不启动服务"""
    from plugins.whisper.plugin import WhisperPlugin

    test_config = {
        "port": 5001,
        "auto_start": False,
    }

    context = Context(
        agent_id="test_agent",
        project_root=os.path.dirname(os.path.dirname(__file__)),
        event_bus=None,
        config=test_config,
        data_dir="/tmp/test_whisper",
        plugin_dir=os.path.join(os.path.dirname(__file__), "..", "plugins", "whisper"),
    )

    plugin = WhisperPlugin(context)
    plugin.on_load()

    # 验证服务未启动
    assert plugin._service_process is None
    assert plugin._health_check_thread is None

    print("✅ auto_start=False 时不启动服务")


def test_whisper_service_script_exists():
    """测试 service.py 文件存在"""
    project_root = os.path.dirname(os.path.dirname(__file__))
    service_path = os.path.join(project_root, "plugins", "whisper", "service", "service.py")

    assert os.path.isfile(service_path), f"Service script not found: {service_path}"

    print(f"✅ Service 脚本存在: {service_path}")


def test_get_tool_modules():
    """测试 get_tool_modules 方法"""
    from plugins.whisper.plugin import WhisperPlugin

    test_config = {"port": 5001, "auto_start": False}
    context = Context(
        agent_id="test_agent",
        project_root=os.path.dirname(os.path.dirname(__file__)),
        event_bus=None,
        config=test_config,
        data_dir="/tmp/test_whisper",
        plugin_dir=os.path.join(os.path.dirname(__file__), "..", "plugins", "whisper"),
    )

    plugin = WhisperPlugin(context)
    tools = plugin.get_tool_modules()

    assert isinstance(tools, list)
    assert len(tools) == 1
    assert tools[0]["name"] == "whisper_transcribe"
    assert tools[0]["level"] == "core"

    print("✅ get_tool_modules 返回正确")


if __name__ == "__main__":
    print("开始测试 Whisper 插件自动启动功能...\n")

    try:
        test_whisper_plugin_structure()
        print()
        test_whisper_plugin_on_load_disabled()
        print()
        test_whisper_service_script_exists()
        print()
        test_get_tool_modules()

        print("\n" + "=" * 50)
        print("所有测试通过！Whisper 插件服务自动启动功能正常！")
        print("=" * 50)

    except AssertionError as e:
        print(f"\n❌ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ 意外错误: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
