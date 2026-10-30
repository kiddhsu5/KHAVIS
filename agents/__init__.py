"""Multi-agent collaboration layer for the K.H.A.V.I.S. (Phase 3).

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

from __future__ import annotations

from .graph import build_graph, run_team
from .roles import Role, create_role, list_roles, load_role
from .state import TeamState, add_attribution, log_step

__all__ = [
    "build_graph",
    "run_team",
    "TeamState",
    "add_attribution",
    "log_step",
    "Role",
    "list_roles",
    "load_role",
    "create_role",
]

__version__ = "0.5.0"
