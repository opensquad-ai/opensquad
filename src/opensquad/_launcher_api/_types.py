"""HandlerState — typed container for all runtime state passed to ManagementHandler."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any


@dataclass
class HandlerState:
    """Typed container for all runtime state that ``create_management_handler``
    passes into the ``ManagementHandler`` closure.

    Using a single dataclass instead of 20+ individual closure variables
    makes the handler methods easier to extract into separate modules.
    """

    procesos: dict[str, Any] = field(default_factory=dict)
    plug_svcs: dict[str, Any] = field(default_factory=dict)
    task_hb: dict[str, dict[str, Any]] = field(default_factory=dict)
    task_sn: set = field(default_factory=set)
    shut_ev: Any = None
    ws_mig: dict[str, Any] = field(default_factory=dict)

    agents_dir: str = ""
    plugins_dir: str = ""
    skills_dir: str = ""
    role_cards_dir: str = ""
    collab_cards_dir: str = ""
    model_cards_dir: str = ""

    mgmt_port: int = 9600
    stall_thresh: int = 300

    syscfg: Any = None
    logger: Any = None
    launcher_lock: threading.RLock = field(default_factory=threading.RLock)

    read_json: Any = None
    chk_port: Any = None
    res_disc_port: Any = None
    cln_reg: Any = None
    appl_def: Any = None
    val_cfg: Any = None
    disc_agents: Any = None
    disc_plug_svcs: Any = None
    AgentProcess: Any = None
    PluginServiceProcess: Any = None
    builtin_plugins: dict = field(default_factory=dict)
