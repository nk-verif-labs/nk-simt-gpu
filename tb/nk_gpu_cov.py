"""Lightweight functional coverage for the NK-GPU regression.

Bins are sampled from the golden model while it executes the same programs the
DUT runs, so a bin counts only if the stimulus actually exercised that
behaviour. "Required" bins must all be hit for the regression to pass;
"informational" bins (operand-class crosses) are only reported.
"""

from collections import defaultdict
from typing import Dict, List

from nk_gpu_model import (
    OP_ADD, OP_ADDI, OP_AND, OP_HALT, OP_LD, OP_MOVI, OP_MUL, OP_NOP, OP_OR,
    OP_SETPX, OP_SHL, OP_SHR, OP_ST, OP_SUB,
)

OP_NAMES = {
    OP_NOP: "NOP", OP_MOVI: "MOVI", OP_ADD: "ADD", OP_SUB: "SUB", OP_MUL: "MUL",
    OP_ADDI: "ADDI", OP_AND: "AND", OP_OR: "OR", OP_SHR: "SHR", OP_SHL: "SHL",
    OP_LD: "LD", OP_ST: "ST", OP_SETPX: "SETPX", OP_HALT: "HALT",
    0xD: "UNDEF_D", 0xE: "UNDEF_E",
}
ALU2 = (OP_ADD, OP_SUB, OP_MUL, OP_AND, OP_OR)


def _cls(v: int) -> str:
    if v == 0:
        return "zero"
    if v == 1:
        return "one"
    if v == 0xFFFF:
        return "max"
    if v == 0x8000:
        return "msb"
    return "other"


class Coverage:
    def __init__(self):
        self.required: Dict[str, int] = {}
        self.info: Dict[str, int] = defaultdict(int)
        self._declare()

    # -------------------------------------------------------------- declare
    def _req(self, name: str):
        self.required.setdefault(name, 0)

    def _declare(self):
        for op, n in OP_NAMES.items():
            self._req(f"opcode/{n}")
        for r in range(8):
            self._req(f"rd/R{r}")
            self._req(f"rs1/R{r}")
            self._req(f"rs2/R{r}")
        for amt in range(16):
            self._req(f"shl_amount/{amt}")
            self._req(f"shr_amount/{amt}")
        for n in ("add_carry_out", "sub_borrow", "mul_overflow", "mul_by_zero",
                  "ld_addr_wrap", "st_addr_wrap", "st_lane_conflict", "st_lane_distinct",
                  "setpx_x_gt_255", "setpx_imm_gt_255", "setpx_rgb_extremes",
                  "write_to_special_reg", "warp_id_zero", "warp_id_ffff", "warp_id_other",
                  "launch_setpx_0", "launch_setpx_1", "launch_setpx_2", "launch_setpx_3plus",
                  "launch_len_ge_24"):
            self._req(n)

    # --------------------------------------------------------------- sample
    def sample_instr(self, op, rd, rs1, rs2, imm, a: List[int], b: List[int]):
        self.required[f"opcode/{OP_NAMES[op]}"] += 1
        self.required[f"rd/R{rd}"] += 1
        self.required[f"rs1/R{rs1}"] += 1
        self.required[f"rs2/R{rs2}"] += 1

        writes_reg = op in (OP_MOVI, OP_ADD, OP_SUB, OP_MUL, OP_ADDI, OP_AND, OP_OR,
                            OP_SHR, OP_SHL, OP_LD)
        if writes_reg and rd < 3:
            self.required["write_to_special_reg"] += 1

        if op in (OP_SHL, OP_SHR):
            key = "shl_amount" if op == OP_SHL else "shr_amount"
            self.required[f"{key}/{imm & 0xF}"] += 1

        if op in ALU2:
            for l in range(4):
                self.info[f"{OP_NAMES[op]}/{_cls(a[l])}x{_cls(b[l])}"] += 1
                if op == OP_ADD and a[l] + b[l] > 0xFFFF:
                    self.required["add_carry_out"] += 1
                if op == OP_SUB and a[l] < b[l]:
                    self.required["sub_borrow"] += 1
                if op == OP_MUL:
                    if a[l] * b[l] > 0xFFFF:
                        self.required["mul_overflow"] += 1
                    if a[l] == 0 or b[l] == 0:
                        self.required["mul_by_zero"] += 1

        if op in (OP_LD, OP_ST):
            addrs = [(a[l] + imm) & 0x7F for l in range(4)]
            if any(((a[l] & 0x7F) + (imm & 0x7F)) > 0x7F for l in range(4)):
                self.required["ld_addr_wrap" if op == OP_LD else "st_addr_wrap"] += 1
            if op == OP_ST:
                key = "st_lane_conflict" if len(set(addrs)) < 4 else "st_lane_distinct"
                self.required[key] += 1

        if op == OP_SETPX:
            if any(v > 0xFF for v in a):
                self.required["setpx_x_gt_255"] += 1
            if imm > 0xFF:
                self.required["setpx_imm_gt_255"] += 1
            if any(v in (0x0000, 0xFFFF, 0xF800, 0x07E0, 0x001F) for v in b):
                self.required["setpx_rgb_extremes"] += 1

    def sample_launch(self, warp_id: int, instr_count: int, setpx_count: int):
        if warp_id == 0:
            self.required["warp_id_zero"] += 1
        elif warp_id == 0xFFFF:
            self.required["warp_id_ffff"] += 1
        else:
            self.required["warp_id_other"] += 1
        key = f"launch_setpx_{setpx_count}" if setpx_count < 3 else "launch_setpx_3plus"
        self.required[key] += 1
        if instr_count >= 24:
            self.required["launch_len_ge_24"] += 1

    # --------------------------------------------------------------- report
    def missing(self) -> List[str]:
        return sorted(k for k, v in self.required.items() if v == 0)

    def percent(self) -> float:
        hit = sum(1 for v in self.required.values() if v)
        return 100.0 * hit / len(self.required)

    def report(self, log=print):
        hit = sum(1 for v in self.required.values() if v)
        log(f"functional coverage: {hit}/{len(self.required)} required bins "
            f"({self.percent():.1f}%)")
        miss = self.missing()
        if miss:
            log("  MISSING: " + ", ".join(miss))
        total_cross = 5 * 25
        got_cross = len(self.info)
        log(f"  operand-class crosses (informational): {got_cross}/{total_cross}")
