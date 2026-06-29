#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
查看Agent提示词和上下文的便捷工具

用法:
    python scripts/view_prompt.py [选项]

选项:
    --agent AGENT_NAME    指定agent名称（默认：coder）
    --system              只显示系统提示词
    --context             只显示最新的用户上下文
    --all                 显示完整会话
    --template            显示提示词模板（base.md + role.md）
    --stats               显示统计信息
"""
import json
import os
import sys
import argparse
from pathlib import Path

# 添加项目根目录到路径
ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))


def get_session_file(agent_name: str) -> Path:
    """获取会话文件路径"""
    return ROOT_DIR / "agents" / agent_name / "data" / "sessions" / "current_session.json"


def load_session(agent_name: str) -> dict:
    """加载会话数据"""
    session_file = get_session_file(agent_name)
    if not session_file.exists():
        print(f"[ERROR] 会话文件不存在: {session_file}")
        return None
    
    with open(session_file, 'r', encoding='utf-8') as f:
        return json.load(f)


def show_system_prompt(session: dict):
    """显示系统提示词"""
    messages = session.get('messages', [])
    system_msg = next((m for m in messages if m['role'] == 'system'), None)
    
    if not system_msg:
        print("[ERROR] 未找到系统提示词")
        return
    
    content = system_msg['content']
    print("=" * 80)
    print("[SYSTEM] 系统提示词 (System Prompt)")
    print("=" * 80)
    print(content)
    print("\n" + "=" * 80)
    print(f"[STATS] 长度: {len(content)} 字符")


def show_latest_context(session: dict):
    """显示最新的用户上下文"""
    messages = session.get('messages', [])
    user_msgs = [m for m in messages if m['role'] == 'user']
    
    if not user_msgs:
        print("[ERROR] 未找到用户消息")
        return
    
    latest = user_msgs[-1]
    content = latest['content']
    
    print("=" * 80)
    print("[USER] 最新用户消息（包含动态上下文）")
    print("=" * 80)
    print(content)
    print("\n" + "=" * 80)
    print(f"[STATS] 长度: {len(content)} 字符")


def show_all_messages(session: dict):
    """显示完整会话"""
    messages = session.get('messages', [])
    
    print("=" * 80)
    print(f"[MESSAGES] 完整会话 (共 {len(messages)} 条消息)")
    print("=" * 80)
    
    for i, msg in enumerate(messages):
        role = msg['role']
        content = msg['content']
        
        role_emoji = {
            'system': '[ASSISTANT]',
            'user': '[USER]',
            'assistant': '🤵'
        }
        
        print(f"\n{role_emoji.get(role, '❓')} [{i+1}] {role.upper()}")
        print("-" * 80)
        
        # 限制显示长度
        if len(content) > 500:
            print(content[:500] + f"\n... (省略 {len(content)-500} 字符)")
        else:
            print(content)
        
        print(f"\n[LENGTH] 长度: {len(content)} 字符")


def show_templates(agent_name: str):
    """显示提示词模板"""
    base_prompt = ROOT_DIR / "prompts" / "base.md"
    role_prompt = ROOT_DIR / "agents" / agent_name / "role.md"
    
    print("=" * 80)
    print("📄 提示词模板")
    print("=" * 80)
    
    if base_prompt.exists():
        with open(base_prompt, 'r', encoding='utf-8') as f:
            content = f.read()
        print("\n🔷 基础提示词 (prompts/base.md)")
        print("-" * 80)
        print(content[:1000] + "..." if len(content) > 1000 else content)
        print(f"\n[LENGTH] 完整长度: {len(content)} 字符")
    
    if role_prompt.exists():
        with open(role_prompt, 'r', encoding='utf-8') as f:
            content = f.read()
        print("\n\n🔷 角色定义 (agents/{}/role.md)".format(agent_name))
        print("-" * 80)
        print(content[:1000] + "..." if len(content) > 1000 else content)
        print(f"\n[LENGTH] 完整长度: {len(content)} 字符")


def show_stats(session: dict, agent_name: str):
    """显示统计信息"""
    messages = session.get('messages', [])
    events = session.get('events', [])
    
    system_count = sum(1 for m in messages if m['role'] == 'system')
    user_count = sum(1 for m in messages if m['role'] == 'user')
    assistant_count = sum(1 for m in messages if m['role'] == 'assistant')
    
    total_chars = sum(len(m['content']) for m in messages)
    
    print("=" * 80)
    print(f"[STATS] 会话统计 (Agent: {agent_name})")
    print("=" * 80)
    print(f"会话ID: {session.get('id', 'N/A')}")
    print(f"创建时间: {session.get('created_at', 'N/A')}")
    print(f"最后更新: {session.get('last_updated', 'N/A')}")
    print()
    print(f"[MESSAGES] 消息总数: {len(messages)}")
    print(f"  - System: {system_count}")
    print(f"  - User: {user_count}")
    print(f"  - Assistant: {assistant_count}")
    print()
    print(f"[EVENTS] 事件总数: {len(events)}")
    
    event_types = {}
    for e in events:
        etype = e.get('type', 'unknown')
        event_types[etype] = event_types.get(etype, 0) + 1
    
    for etype, count in sorted(event_types.items()):
        print(f"  - {etype}: {count}")
    
    print()
    print(f"[LENGTH] 总字符数: {total_chars:,}")
    print(f"[LENGTH] 平均每条消息: {total_chars // len(messages) if messages else 0:,} 字符")


def main():
    parser = argparse.ArgumentParser(description='查看Agent提示词和上下文')
    parser.add_argument('--agent', default='coder', help='Agent名称（默认：coder）')
    parser.add_argument('--system', action='store_true', help='只显示系统提示词')
    parser.add_argument('--context', action='store_true', help='只显示最新用户上下文')
    parser.add_argument('--all', action='store_true', help='显示完整会话')
    parser.add_argument('--template', action='store_true', help='显示提示词模板')
    parser.add_argument('--stats', action='store_true', help='显示统计信息')
    
    args = parser.parse_args()
    
    # 如果没有指定任何选项，显示统计信息
    if not any([args.system, args.context, args.all, args.template, args.stats]):
        args.stats = True
    
    # 处理模板显示（不需要会话文件）
    if args.template:
        show_templates(args.agent)
        return
    
    # 加载会话
    session = load_session(args.agent)
    if not session:
        sys.exit(1)
    
    # 根据选项显示内容
    if args.stats:
        show_stats(session, args.agent)
    
    if args.system:
        show_system_prompt(session)
    
    if args.context:
        show_latest_context(session)
    
    if args.all:
        show_all_messages(session)


if __name__ == '__main__':
    main()
