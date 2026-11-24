"""Bit-exact Python Golden Model & Functional Coverage for NK-GPU v1 (16-bit 4-lane SIMT)."""

from dataclasses import dataclass, field


def expand_rgb565_to_888(val16: int) -> int:
    r = ((val16 >> 11) & 0x1F) << 3
    g = ((val16 >> 5) & 0x3F) << 2
    b = (val16 & 0x1F) << 3
    return (r << 16) | (g << 8) | b


@dataclass
class CoverageTracker:
    opcodes_seen: set[int] = field(default_factory=set)
    rd_seen: set[int] = field(default_factory=set)
    rs1_seen: set[int] = field(default_factory=set)
    rs2_seen: set[int] = field(default_factory=set)
    corners: set[str] = field(default_factory=set)

    def summary(self) -> str:
        return (
            f"Opcodes={len(self.opcodes_seen)}/14, "
            f"rd={len(self.rd_seen)}/8, rs1={len(self.rs1_seen)}/8, rs2={len(self.rs2_seen)}/8, "
            f"Corners={sorted(self.corners)}"
        )


class NkGpuGoldenModel:
    def __init__(self):
        self.imem = [0] * 32
        self.dmem = [0] * 128
        self.gpr = [[0] * 8 for _ in range(4)]  # R3..R7 used per lane
        self.pixels: list[tuple[int, int, int]] = []
        self.cov = CoverageTracker()

    def reset(self):
        self.gpr = [[0] * 8 for _ in range(4)]
        self.pixels.clear()

    def read_reg(self, lane: int, idx: int, warp_id: int) -> int:
        if idx == 0:
            return 0
        if idx == 1:
            return lane & 0xFFFF
        if idx == 2:
            return warp_id & 0xFFFF
        return self.gpr[lane][idx] & 0xFFFF

    def run_warp(self, warp_id: int, max_steps: int = 128):
        pc = 0
        for _ in range(max_steps):
            instr = self.imem[pc & 0x1F]
            opcode = (instr >> 28) & 0xF
            rd = (instr >> 24) & 0x7
            rs1 = (instr >> 20) & 0x7
            rs2 = (instr >> 16) & 0x7
            imm16 = instr & 0xFFFF

            self.cov.opcodes_seen.add(opcode)
            self.cov.rd_seen.add(rd)
            self.cov.rs1_seen.add(rs1)
            self.cov.rs2_seen.add(rs2)

            if opcode == 0xF:  # HALT
                return

            if opcode == 0xC:  # SETPX
                y = imm16 & 0xFF
                for lane in range(4):
                    x = self.read_reg(lane, rs1, warp_id) & 0xFF
                    rgb565 = self.read_reg(lane, rs2, warp_id)
                    self.pixels.append((x, y, expand_rgb565_to_888(rgb565)))
                pc = (pc + 1) & 0x3F
                continue

            if opcode == 0xB:  # ST
                addrs = []
                for lane in range(4):
                    va = self.read_reg(lane, rs1, warp_id)
                    vb = self.read_reg(lane, rs2, warp_id)
                    raw_sum = (va & 0x7F) + (imm16 & 0x7F)
                    if raw_sum >= 128:
                        self.cov.corners.add("dmem_addr_wrap")
                    addr = raw_sum & 0x7F
                    addrs.append(addr)
                    self.dmem[addr] = vb & 0xFFFF
                if len(set(addrs)) < 4:
                    self.cov.corners.add("dmem_lane_collision")
                pc = (pc + 1) & 0x3F
                continue

            # ALU / LD instructions
            next_vals = []
            for lane in range(4):
                va = self.read_reg(lane, rs1, warp_id)
                vb = self.read_reg(lane, rs2, warp_id)
                we = False
                res = 0
                if opcode == 0x1:  # MOVI
                    res, we = imm16, True
                elif opcode == 0x2:  # ADD
                    s = va + vb
                    if s > 0xFFFF:
                        self.cov.corners.add("add_overflow")
                    res, we = s & 0xFFFF, True
                elif opcode == 0x3:  # SUB
                    if va < vb:
                        self.cov.corners.add("sub_underflow")
                    res, we = (va - vb) & 0xFFFF, True
                elif opcode == 0x4:  # MUL
                    p = va * vb
                    if p > 0xFFFF:
                        self.cov.corners.add("mul_overflow")
                    res, we = p & 0xFFFF, True
                elif opcode == 0x5:  # ADDI
                    s = va + imm16
                    if s > 0xFFFF:
                        self.cov.corners.add("addi_overflow")
                    res, we = s & 0xFFFF, True
                elif opcode == 0x6:  # AND
                    res, we = va & vb, True
                elif opcode == 0x7:  # OR
                    res, we = va | vb, True
                elif opcode == 0x8:  # SHR
                    sh = imm16 & 0xF
                    if sh == 0:
                        self.cov.corners.add("shift_0")
                    elif sh == 15:
                        self.cov.corners.add("shift_15")
                    res, we = (va >> sh) & 0xFFFF, True
                elif opcode == 0x9:  # SHL
                    sh = imm16 & 0xF
                    if sh == 0:
                        self.cov.corners.add("shift_0")
                    elif sh == 15:
                        self.cov.corners.add("shift_15")
                    res, we = (va << sh) & 0xFFFF, True
                elif opcode == 0xA:  # LD
                    raw_sum = (va & 0x7F) + (imm16 & 0x7F)
                    if raw_sum >= 128:
                        self.cov.corners.add("dmem_addr_wrap")
                    res, we = self.dmem[raw_sum & 0x7F] & 0xFFFF, True
                next_vals.append((we, res))

            for lane, (we, res) in enumerate(next_vals):
                if we:
                    if rd < 3:
                        self.cov.corners.add("ro_reg_write_attempt")
                    else:
                        self.gpr[lane][rd] = res & 0xFFFF

            pc = (pc + 1) & 0x3F

        raise AssertionError(f"Golden model warp {warp_id} did not HALT within {max_steps} steps")
