"""Cycle-independent golden (reference) model of the NK-GPU v1 SIMT core.

The model decodes the raw 32-bit instruction words, exactly like the RTL does,
and tracks the architectural state the host can observe:

  * per-lane general-purpose registers R3..R7 (16-bit)
  * the shared 128 x 16-bit data memory
  * the 32 x 32-bit instruction memory
  * the pixels emitted by SETPX (RGB565 expanded to RGB888)
  * the host status word (busy / done / pc)

Behaviour intentionally mirrors the RTL (rtl/nk_gpu_top.v, rtl/nk_gpu_lane.v),
not the older prose in rtl/nk_gpu_isa.vh:

  * datapath is 16 bits wide; SHL/SHR use imm16[3:0]
  * LD/ST address = (rs1 + imm16) & 0x7F (128-entry DMEM)
  * when several lanes ST to the same address the highest lane wins
  * undefined opcodes (0xD, 0xE) behave as NOP
"""

from typing import List, Optional, Tuple

MASK16 = 0xFFFF
NUM_LANES = 4
IMEM_DEPTH = 32
DMEM_DEPTH = 128

OP_NOP, OP_MOVI, OP_ADD, OP_SUB, OP_MUL = 0x0, 0x1, 0x2, 0x3, 0x4
OP_ADDI, OP_AND, OP_OR, OP_SHR, OP_SHL = 0x5, 0x6, 0x7, 0x8, 0x9
OP_LD, OP_ST, OP_SETPX, OP_HALT = 0xA, 0xB, 0xC, 0xF

Pixel = Tuple[int, int, int]  # (x, y, rgb888)


def rgb565_to_rgb888(c: int) -> int:
    """Same bit-expansion as the RTL: {r5,000, g6,00, b5,000}."""
    r5 = (c >> 11) & 0x1F
    g6 = (c >> 5) & 0x3F
    b5 = c & 0x1F
    return ((r5 << 3) << 16) | ((g6 << 2) << 8) | (b5 << 3)


class ModelError(Exception):
    pass


class GpuModel:
    def __init__(self):
        # Optional callback(op, rd, rs1, rs2, imm, a_per_lane, b_per_lane) used by
        # the functional-coverage collector; called once per executed instruction.
        self.observer = None
        self.reset()

    # ------------------------------------------------------------------ state
    def reset(self):
        """Hardware reset: GPRs, pc, warp id and status clear; memories persist."""
        if not hasattr(self, "imem"):
            self.imem: List[Optional[int]] = [None] * IMEM_DEPTH
            self.dmem: List[Optional[int]] = [None] * DMEM_DEPTH
        self.gpr = [[0] * 8 for _ in range(NUM_LANES)]
        self.warp_id = 0
        self.pc = 0
        self.busy = False
        self.done = False

    def status(self) -> int:
        """Value of host_rdata for host_sel == 0."""
        return (self.pc << 8) | (int(self.done) << 1) | int(self.busy)

    # ------------------------------------------------------------ host access
    def write_imem(self, addr: int, word: int):
        self.imem[addr & 0x1F] = word & 0xFFFFFFFF

    def write_dmem(self, addr: int, value: int):
        self.dmem[addr & 0x7F] = value & MASK16

    def set_warp_id(self, wid: int):
        self.warp_id = wid & MASK16

    # -------------------------------------------------------------- execution
    def _read(self, lane: int, r: int) -> int:
        if r == 0:
            return 0
        if r == 1:
            return lane
        if r == 2:
            return self.warp_id
        return self.gpr[lane][r]

    def _mem(self, addr: int) -> int:
        v = self.dmem[addr]
        if v is None:
            raise ModelError(f"read of uninitialised DMEM[{addr}]")
        return v

    def run(self, warp_id: Optional[int] = None, max_steps: int = 10000) -> List[Pixel]:
        """Launch a warp and run to HALT. Returns the SETPX pixel stream in order."""
        if warp_id is not None:
            self.set_warp_id(warp_id)
        self.busy, self.done, self.pc = True, False, 0
        pixels: List[Pixel] = []

        for _ in range(max_steps):
            word = self.imem[self.pc & 0x1F]
            if word is None:
                raise ModelError(f"fetch of uninitialised IMEM[{self.pc & 0x1F}]")
            op = (word >> 28) & 0xF
            rd = (word >> 24) & 0x7
            rs1 = (word >> 20) & 0x7
            rs2 = (word >> 16) & 0x7
            imm = word & MASK16

            a = [self._read(l, rs1) for l in range(NUM_LANES)]
            b = [self._read(l, rs2) for l in range(NUM_LANES)]

            if self.observer is not None:
                self.observer(op, rd, rs1, rs2, imm, list(a), list(b))

            if op == OP_HALT:
                self.busy, self.done = False, True
                return pixels

            if op == OP_ST:
                for l in range(NUM_LANES):  # ascending lane order: highest lane wins
                    self.dmem[(a[l] + imm) & 0x7F] = b[l]
            elif op == OP_SETPX:
                for l in range(NUM_LANES):
                    pixels.append((a[l] & 0xFF, imm & 0xFF, rgb565_to_rgb888(b[l])))
            else:
                for l in range(NUM_LANES):
                    res = None
                    if op == OP_MOVI:
                        res = imm
                    elif op == OP_ADD:
                        res = a[l] + b[l]
                    elif op == OP_SUB:
                        res = a[l] - b[l]
                    elif op == OP_MUL:
                        res = a[l] * b[l]
                    elif op == OP_ADDI:
                        res = a[l] + imm
                    elif op == OP_AND:
                        res = a[l] & b[l]
                    elif op == OP_OR:
                        res = a[l] | b[l]
                    elif op == OP_SHR:
                        res = a[l] >> (imm & 0xF)
                    elif op == OP_SHL:
                        res = a[l] << (imm & 0xF)
                    elif op == OP_LD:
                        res = self._mem((a[l] + imm) & 0x7F)
                    # NOP and undefined opcodes: res stays None
                    if res is not None and rd >= 3:
                        self.gpr[l][rd] = res & MASK16
            self.pc = (self.pc + 1) & 0x3F

        raise ModelError("program did not HALT")
