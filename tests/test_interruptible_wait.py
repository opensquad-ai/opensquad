#!/usr/bin/env python3
"""
测试 system.wait() 的可中断功能
"""

import asyncio
import os
import sys

import pytest

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.asyncio

from opensquad.sleep_controller import sleep_controller
from opensquad.tools.system import wait


async def test_non_interruptible_wait():
    """测试不可中断的等待（默认行为）"""
    print("\n=== 测试 1: 不可中断的等待 ===")
    print("开始等待 3 秒（不可中断）...")
    result = await wait(3, interruptible=False)
    print(f"结果: {result}")
    assert result["status"] == "success"
    assert "Wait completed" in result["message"]
    print("✓ 不可中断等待测试通过")


async def test_interruptible_wait_natural():
    """测试可中断等待 - 自然醒"""
    print("\n=== 测试 2: 可中断等待 - 自然醒 ===")
    print("开始等待 2 秒（可中断，但不会被打断）...")
    result = await wait(2, interruptible=True)
    print(f"结果: {result}")
    assert result["status"] == "success"
    assert result["wake_type"] == "natural"
    assert result["actual_seconds"] >= 1.9  # 允许一点误差
    print("✓ 可中断等待（自然醒）测试通过")


async def test_interruptible_wait_interrupted():
    """测试可中断等待 - 被打断"""
    print("\n=== 测试 3: 可中断等待 - 被打断 ===")
    print("开始等待 10 秒（可中断），将在 1 秒后被唤醒...")

    # 启动等待任务
    wait_task = asyncio.create_task(wait(10, interruptible=True))

    # 1 秒后唤醒
    await asyncio.sleep(1)
    wake_success = sleep_controller.wake_up("测试唤醒")
    print(f"发送唤醒信号: {wake_success}")

    # 等待任务完成
    result = await wait_task
    print(f"结果: {result}")
    assert result["status"] == "success"
    assert result["wake_type"] == "interrupted"
    assert result["actual_seconds"] < 3  # 应该远小于 10 秒
    assert "测试唤醒" in result["wake_reason"]
    print("✓ 可中断等待（被打断）测试通过")


async def main():
    """运行所有测试"""
    print("=" * 60)
    print("system.wait() 可中断功能测试")
    print("=" * 60)

    try:
        # 测试 1: 不可中断等待
        await test_non_interruptible_wait()

        # 测试 2: 可中断等待 - 自然醒
        await test_interruptible_wait_natural()

        # 测试 3: 可中断等待 - 被打断
        await test_interruptible_wait_interrupted()

        print("\n" + "=" * 60)
        print("✓ 所有测试通过！")
        print("=" * 60)

    except Exception as e:
        print(f"\n✗ 测试失败: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
