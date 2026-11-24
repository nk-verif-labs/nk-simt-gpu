"""Directed + constrained-random verification of nk_gpu_top against a golden model.

Every program that runs on the DUT also runs on `GpuModel`; after each launch the
testbench compares

  * the full 128-entry DMEM (registers R3..R7 are made visible by dumping them to
    DMEM[108..127] with `dump_regs()`),
  * the exact SETPX pixel stream seen on px_valid/px_x/px_y/px_rgb,
  * the host status word (busy/done/pc).

Directed tests additionally assert hand-computed expectations so a bug shared by
the model and the RTL cannot hide.

Environment knobs:
  NK_SEED             seed of the random regression (default 1; logged)
  NK_RAND_PROGRAMS    number of random programs, 2 launches each (default 80)
  NK_COV_STRICT       1 (default): fail if a required coverage bin is missed
"""

import os
import random
import sys

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "driver"))
sys.path.insert(0, HERE)

from nk_gpu_asm import encode  # noqa: E402
from nk_gpu_cov import Coverage  # noqa: E402
from nk_gpu_model import GpuModel, OP_HALT, OP_SETPX  # noqa: E402
from test_nk_gpu import launch_warp, mmio_read, mmio_write, reset_dut  # noqa: E402

M16 = 0xFFFF
COV = Coverage()  # shared by every test in this module
DUMP_BASE = 108  # DMEM[108..127] <- R3..R7 of lanes 0..3 (see dump_regs)


# --------------------------------------------------------------------------- helpers
def raw_word(op: int, rd=0, rs1=0, rs2=0, imm=0) -> int:
    return ((op & 0xF) << 28) | ((rd & 7) << 24) | ((rs1 & 7) << 20) | ((rs2 & 7) << 16) | (imm & M16)


def dump_regs():
    """Five STs that expose R3..R7 of every lane at DMEM[DUMP_BASE + 4*j + lane]."""
    return [encode("ST", rs1="TID", rs2=f"R{3 + j}", imm16=DUMP_BASE + 4 * j) for j in range(5)]


def dumped(mem, reg: int, lane: int) -> int:
    return mem[DUMP_BASE + 4 * (reg - 3) + lane]


HALT = encode("HALT")


class Bench:
    """DUT + golden model + pixel monitor + checker."""

    def __init__(self, dut):
        self.dut = dut
        self.model = GpuModel()
        self.pixels = []       # (x, y, rgb888) for every px_valid cycle
        self.valid_trace = []  # px_valid per clock, for protocol checks

    async def start(self, init_dmem: bool = True):
        dut = self.dut
        cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
        await reset_dut(dut)
        self.model.reset()
        cocotb.start_soon(self._monitor())
        if init_dmem:
            await self.load_dmem([0] * 128)

    async def _monitor(self):
        dut = self.dut
        while True:
            await ClockCycles(dut.clk, 1)
            v = int(dut.px_valid.value)
            self.valid_trace.append(v)
            if v:
                self.pixels.append((int(dut.px_x.value), int(dut.px_y.value), int(dut.px_rgb.value)))

    def clear_trace(self):
        self.pixels.clear()
        self.valid_trace.clear()

    async def load_program(self, words):
        assert len(words) <= 32, "program does not fit in IMEM"
        for pc, w in enumerate(words):
            await mmio_write(self.dut, 1, pc, w)
            self.model.write_imem(pc, w)

    async def load_dmem(self, values):
        assert len(values) == 128
        for a, v in enumerate(values):
            await mmio_write(self.dut, 2, a, v)
            self.model.write_dmem(a, v)

    async def run(self, warp_id: int):
        """Launch on DUT, run the model, compare everything. Returns the expected pixels."""
        dut = self.dut
        self.clear_trace()
        await launch_warp(dut, warp_id, timeout=800)
        await ClockCycles(dut.clk, 3)
        got_pixels = list(self.pixels)

        stats = {"instr": 0, "setpx": 0}

        def observe(op, rd, rs1, rs2, imm, a, b):
            stats["instr"] += 1
            stats["setpx"] += int(op == OP_SETPX)
            COV.sample_instr(op, rd, rs1, rs2, imm, a, b)

        self.model.observer = observe
        expected = self.model.run(warp_id)
        self.model.observer = None
        COV.sample_launch(warp_id, stats["instr"], stats["setpx"])

        assert got_pixels == expected, _pixel_diff(got_pixels, expected)

        status = await mmio_read(dut, 0, 0)
        assert status == self.model.status(), f"status {status:#06x} != model {self.model.status():#06x}"

        bad = []
        for a in range(128):
            v = await mmio_read(dut, 2, a)
            if v != self.model.dmem[a]:
                bad.append((a, v, self.model.dmem[a]))
        assert not bad, "DMEM mismatches (addr, dut, model): " + ", ".join(
            f"[{a}] {d:#06x}/{m:#06x}" for a, d, m in bad[:8])
        assert len(self.pixels) == len(got_pixels), "unexpected pixels emitted after HALT"
        return expected

    @property
    def mem(self):
        return self.model.dmem


def _pixel_diff(got, exp):
    if len(got) != len(exp):
        return f"pixel count: dut {len(got)} vs model {len(exp)}"
    for i, (g, e) in enumerate(zip(got, exp)):
        if g != e:
            return f"pixel #{i}: dut (x={g[0]}, y={g[1]}, rgb={g[2]:#08x}) vs model (x={e[0]}, y={e[1]}, rgb={e[2]:#08x})"
    return "pixel streams differ"


# ----------------------------------------------------------------- directed tests
@cocotb.test()
async def test_alu_corner_cases(dut):
    """ADD/SUB/MUL/AND/OR on boundary operands, checked against Python arithmetic."""
    b = Bench(dut)
    await b.start()

    pairs = [(0, 0), (0xFFFF, 1), (0x8000, 0x8000), (0xFFFF, 0xFFFF), (0x1234, 0xABCD), (1, 0xFFFF)]
    ref = {
        "ADD": lambda x, y: (x + y) & M16,
        "SUB": lambda x, y: (x - y) & M16,
        "MUL": lambda x, y: (x * y) & M16,
        "AND": lambda x, y: x & y,
        "OR": lambda x, y: x | y,
    }
    for name, fn in ref.items():
        prog = []
        for i, (x, y) in enumerate(pairs):
            prog += [
                encode("MOVI", rd="R3", imm16=x),
                encode("MOVI", rd="R4", imm16=y),
                encode(name, rd="R5", rs1="R3", rs2="R4"),
                encode("ST", rs1="TID", rs2="R5", imm16=4 * i),
            ]
        await b.load_program(prog + [HALT])
        await b.run(warp_id=0x00A5)
        for i, (x, y) in enumerate(pairs):
            for lane in range(4):
                got = b.mem[4 * i + lane]
                assert got == fn(x, y), f"{name} {x:#x},{y:#x} lane {lane}: got {got:#x}, exp {fn(x, y):#x}"


@cocotb.test()
async def test_shifts_and_immediates(dut):
    """SHL/SHR by every amount (upper imm bits set must be ignored), ADDI/MOVI wrap-around."""
    b = Bench(dut)
    await b.start()

    for name, fn in (("SHL", lambda v, n: (v << n) & M16), ("SHR", lambda v, n: v >> n)):
        for chunk in (range(0, 12), range(12, 16)):
            prog = [encode("MOVI", rd="R3", imm16=0x8001)]
            for slot, amt in enumerate(chunk):
                prog += [
                    encode(name, rd="R4", rs1="R3", imm16=0xFFE0 | amt),  # bits 15:5 set, must not matter
                    encode("ST", rs1="TID", rs2="R4", imm16=4 * slot),
                ]
            await b.load_program(prog + [HALT])
            await b.run(warp_id=3)
            for slot, amt in enumerate(chunk):
                for lane in range(4):
                    assert b.mem[4 * slot + lane] == fn(0x8001, amt), f"{name} by {amt} lane {lane}"

    prog = [
        encode("MOVI", rd="R3", imm16=0xFFFF),
        encode("ADDI", rd="R4", rs1="R3", imm16=1),        # wraps to 0
        encode("ADDI", rd="R5", rs1="R3", imm16=0xFFFF),   # 0xFFFE
        encode("ADDI", rd="R6", rs1="TID", imm16=100),     # 100 + lane
        encode("ADDI", rd="R7", rs1="R0", imm16=0x7FFF),   # plain constant
    ] + dump_regs() + [HALT]
    await b.load_program(prog)
    await b.run(warp_id=1)
    for lane in range(4):
        assert dumped(b.mem, 3, lane) == 0xFFFF
        assert dumped(b.mem, 4, lane) == 0
        assert dumped(b.mem, 5, lane) == 0xFFFE
        assert dumped(b.mem, 6, lane) == 100 + lane
        assert dumped(b.mem, 7, lane) == 0x7FFF


@cocotb.test()
async def test_special_registers(dut):
    """R0 reads 0, R1 = lane id, R2 = warp id; writes to R0..R2 are ignored."""
    b = Bench(dut)
    await b.start()

    prog = [
        encode("MOVI", rd="R0", imm16=0x1234),
        encode("MOVI", rd="R1", imm16=0x0055),
        encode("MOVI", rd="R2", imm16=0x0066),
        encode("ADD", rd="R3", rs1="R0", rs2="R0"),
        encode("ADD", rd="R4", rs1="R1", rs2="R0"),
        encode("ADD", rd="R5", rs1="R2", rs2="R0"),
        encode("ADD", rd="R6", rs1="R1", rs2="R2"),
    ] + dump_regs() + [HALT]
    await b.load_program(prog)
    for wid in (0x0000, 0xBEEF, 0xFFFF):
        await b.run(warp_id=wid)
        for lane in range(4):
            assert dumped(b.mem, 3, lane) == 0, "R0 must read as zero"
            assert dumped(b.mem, 4, lane) == lane, "R1 must be the lane id"
            assert dumped(b.mem, 5, lane) == wid, "R2 must be the warp id"
            assert dumped(b.mem, 6, lane) == (lane + wid) & M16


@cocotb.test()
async def test_memory_addressing(dut):
    """LD/ST address wrap at 128 words, per-lane addressing and same-address lane conflicts."""
    b = Bench(dut)
    await b.start(init_dmem=False)
    await b.load_dmem([0x1000 + i for i in range(128)])

    prog = [
        encode("LD", rd="R3", rs1="TID", imm16=126),         # lanes read 126, 127, 0, 1
        encode("ST", rs1="TID", rs2="R3", imm16=20),         # DMEM[20+lane] = loaded value
        encode("ST", rs1="R0", rs2="TID", imm16=50),         # all lanes hit DMEM[50]
        encode("MOVI", rd="R4", imm16=0x2000),
        encode("ADD", rd="R4", rs1="R4", rs2="TID"),
        encode("ST", rs1="TID", rs2="R4", imm16=126),        # writes 126, 127, 0, 1
        HALT,
    ]
    await b.load_program(prog)
    await b.run(warp_id=0)
    for lane in range(4):
        assert b.mem[20 + lane] == 0x1000 + ((126 + lane) & 0x7F)
    assert b.mem[50] == 3, "same-address ST: highest lane must win"
    assert [b.mem[126], b.mem[127], b.mem[0], b.mem[1]] == [0x2000, 0x2001, 0x2002, 0x2003]
    assert b.mem[2] == 0x1002, "untouched DMEM must keep its value"


@cocotb.test()
async def test_setpx_protocol(dut):
    """SETPX: 4 contiguous valid cycles per instruction, x/y truncation, RGB565->RGB888."""
    b = Bench(dut)
    await b.start()

    prog = [
        encode("MOVI", rd="R3", imm16=0x1FC),
        encode("ADD", rd="R3", rs1="R3", rs2="TID"),         # x = 0x1FC..0x1FF -> truncates to 0xFC..0xFF
        encode("MOVI", rd="R4", imm16=0xFFFF),
        encode("MOVI", rd="R5", imm16=0x07E0),
        encode("ADD", rd="R5", rs1="R5", rs2="TID"),         # pure green + lane in the blue field
        encode("SETPX", rs1="R3", rs2="R4", imm16=0x1AB),    # y = 0xAB
        encode("SETPX", rs1="R3", rs2="R5", imm16=7),
        encode("SETPX", rs1="R0", rs2="R0", imm16=0),
        HALT,
    ]
    await b.load_program(prog)
    got = await b.run(warp_id=0)

    xs = [0xFC, 0xFD, 0xFE, 0xFF]
    explicit = (
        [(xs[l], 0xAB, 0xF8FCF8) for l in range(4)]
        + [(xs[l], 7, 0x00FC00 | (l << 3)) for l in range(4)]
        + [(0, 0, 0)] * 4
    )
    assert got == explicit, "golden model disagrees with hand-computed pixels"

    runs, cur = [], 0
    for v in b.valid_trace:
        if v:
            cur += 1
        elif cur:
            runs.append(cur)
            cur = 0
    if cur:
        runs.append(cur)
    assert runs == [4, 4, 4], f"px_valid must be high for exactly 4 contiguous cycles per SETPX, got {runs}"


@cocotb.test()
async def test_nop_and_undefined_opcodes(dut):
    """NOP and the undefined opcodes 0xD/0xE advance the pc without side effects."""
    b = Bench(dut)
    await b.start()

    prog = [
        encode("MOVI", rd="R3", imm16=7),
        encode("NOP", rd="R3", rs1="R3", rs2="R3", imm16=0x1234),
        raw_word(0xD, rd=3, rs1=3, rs2=3, imm=0x1234),
        raw_word(0xE, rd=3, rs1=3, rs2=3, imm=0x5678),
    ] + dump_regs() + [HALT]
    await b.load_program(prog)
    await b.run(warp_id=0)
    for lane in range(4):
        assert dumped(b.mem, 3, lane) == 7
    st = await mmio_read(dut, 0, 0)
    assert st == (len(prog) - 1) << 8 | 0x2, "pc must point at HALT and done must be set"


@cocotb.test()
async def test_host_interface(dut):
    """Status register, memory read-back, unmapped select, sticky done, busy lock-out."""
    b = Bench(dut)
    await b.start(init_dmem=False)

    assert await mmio_read(dut, 0, 0) == 0, "status must be idle after reset"

    # memory read-back
    pattern_i = [0xFFFFFFFF, 0x00000000, 0xA5A5A5A5, 0x5A5A5A5A]
    for i, w in enumerate(pattern_i):
        await mmio_write(dut, 1, 28 + i, w)
        assert await mmio_read(dut, 1, 28 + i) == w
    for a, v in ((0, 0xFFFF), (1, 0x0000), (127, 0x8001), (64, 0x1234)):
        await mmio_write(dut, 2, a, v)
        assert await mmio_read(dut, 2, a) == v

    # unmapped select reads zero and ignores writes
    for addr in (0, 1, 31, 200):
        assert await mmio_read(dut, 3, addr) == 0
    await mmio_write(dut, 3, 64, 0xDEAD)
    assert await mmio_read(dut, 2, 64) == 0x1234
    assert await mmio_read(dut, 0, 0) == 0

    # a control write without the start bit must not launch
    await mmio_write(dut, 0, 0, 0xABCD << 16)
    await ClockCycles(dut.clk, 3)
    assert await mmio_read(dut, 0, 0) == 0

    # done is sticky and pc points at HALT; relaunch clears done immediately
    for i in range(8):
        await mmio_write(dut, 1, i, encode("NOP"))
    await mmio_write(dut, 1, 8, HALT)
    await launch_warp(dut, 0)
    assert await mmio_read(dut, 0, 0) == (8 << 8) | 0x2
    await ClockCycles(dut.clk, 5)
    assert await mmio_read(dut, 0, 0) == (8 << 8) | 0x2, "done must stay set"
    await mmio_write(dut, 0, 0, 0x1)
    st = await mmio_read(dut, 0, 0)
    assert st & 0x3 == 0x1, f"launch must set busy and clear done, status {st:#06x}"
    while not (await mmio_read(dut, 0, 0)) & 0x2:
        pass

    # host writes are ignored while busy
    wid = 0x005A
    prog = [encode("SETPX", rs1="WID", rs2="R0", imm16=i) for i in range(6)] + [HALT]
    for pc, w in enumerate(prog):
        await mmio_write(dut, 1, pc, w)
    await mmio_write(dut, 2, 5, 0x1111)
    b.clear_trace()
    await mmio_write(dut, 0, 0, (wid << 16) | 1)           # launch
    await mmio_write(dut, 2, 5, 0xBEEF)                    # all three must be ignored
    await mmio_write(dut, 1, 1, HALT)
    await mmio_write(dut, 0, 0, 0x77 << 16)
    assert await mmio_read(dut, 0, 0) & 0x1, "GPU should still be busy"
    while not (await mmio_read(dut, 0, 0)) & 0x2:
        pass
    await ClockCycles(dut.clk, 3)
    assert await mmio_read(dut, 2, 5) == 0x1111, "DMEM write while busy must be ignored"
    assert await mmio_read(dut, 1, 1) == prog[1], "IMEM write while busy must be ignored"
    exp = [(wid & 0xFF, i, 0) for i in range(6) for _ in range(4)]
    assert b.pixels == exp, "program must run to completion unchanged (warp id unchanged too)"


@cocotb.test()
async def test_reset_during_run(dut):
    """A reset mid-run aborts the warp, clears GPRs/status/pixel output, keeps memories."""
    b = Bench(dut)
    await b.start()

    # 1. leave non-zero values in R3..R7
    prog = [encode("MOVI", rd=f"R{r}", imm16=0x1100 + r) for r in range(3, 8)] + [HALT]
    await b.load_program(prog)
    await b.run(warp_id=9)

    # 2. start a long SETPX program and reset it half-way
    long_prog = [encode("SETPX", rs1="TID", rs2="R3", imm16=i) for i in range(8)] + [HALT]
    await b.load_program(long_prog)
    await mmio_write(dut, 0, 0, (4 << 16) | 1)
    await ClockCycles(dut.clk, 12)
    assert await mmio_read(dut, 0, 0) & 0x1, "should be mid-run"
    await reset_dut(dut)
    assert int(dut.px_valid.value) == 0
    assert await mmio_read(dut, 0, 0) == 0, "status must be idle after reset"
    await ClockCycles(dut.clk, 20)
    b.clear_trace()
    b.model.reset()

    # 3. GPRs must be zero again: dump them
    await b.load_program(dump_regs() + [HALT])
    await b.run(warp_id=0)
    for lane in range(4):
        for r in range(3, 8):
            assert dumped(b.mem, r, lane) == 0, f"R{r} lane {lane} not cleared by reset"


# ----------------------------------------------------------- constrained random
_OPS = ["MOVI", "ADD", "SUB", "MUL", "ADDI", "AND", "OR", "SHR", "SHL", "LD", "ST", "SETPX", "NOP", "UNDEF"]
_WEIGHTS = [14, 10, 10, 8, 8, 6, 6, 7, 7, 9, 8, 5, 2, 1]
_CORNERS = [0, 1, 2, 0x7F, 0x80, 0x7FFF, 0x8000, 0xFFFF, 0x00FF, 0xFF00, 0x5555, 0xAAAA]
_REGS = [f"R{i}" for i in range(8)]


def _rnd_val(rng: random.Random) -> int:
    return rng.choice(_CORNERS) if rng.random() < 0.6 else rng.getrandbits(16)


def _gen_instr(rng: random.Random, shamt: dict) -> int:
    op = rng.choices(_OPS, _WEIGHTS)[0]
    rd = rng.choice(["R0", "R1", "R2"]) if rng.random() < 0.15 else rng.choice(_REGS[3:])
    rs1, rs2 = rng.choice(_REGS), rng.choice(_REGS)
    imm = _rnd_val(rng)
    if op in ("SHL", "SHR"):  # round-robin amount guarantees all 16 are exercised
        amt = shamt[op] % 16
        shamt[op] += 1
        imm = amt | (rng.getrandbits(12) << 4 if rng.random() < 0.5 else 0)
    if op == "UNDEF":
        return raw_word(rng.choice([0xD, 0xE]), rng.randint(4, 7), rng.getrandbits(3),
                        rng.getrandbits(3), rng.getrandbits(16))
    return encode(op, rd=rd, rs1=rs1, rs2=rs2, imm16=imm)


def _rnd_warp(rng: random.Random) -> int:
    r = rng.random()
    return 0 if r < 0.1 else 0xFFFF if r < 0.2 else rng.getrandbits(16)


@cocotb.test()
async def test_random_regression(dut):
    """Random programs x 2 launches (registers and DMEM carry over) vs the golden model."""
    seed = int(os.environ.get("NK_SEED", "1"))
    n_prog = int(os.environ.get("NK_RAND_PROGRAMS", "80"))
    dut._log.info(f"random regression: seed={seed} programs={n_prog}")
    rng = random.Random(seed)

    b = Bench(dut)
    await b.start()
    shamt = {"SHL": 0, "SHR": 0}

    for p in range(n_prog):
        await b.load_dmem([_rnd_val(rng) for _ in range(128)])
        body = [_gen_instr(rng, shamt) for _ in range(rng.randint(3, 25))]
        await b.load_program(body + dump_regs() + [HALT])
        for launch in range(2):
            wid = _rnd_warp(rng)
            try:
                await b.run(wid)
            except AssertionError as e:
                raise AssertionError(f"seed={seed} program={p} launch={launch} warp={wid:#06x}: {e}") from e
    COV.report(dut._log.info)


@cocotb.test()
async def test_zz_functional_coverage(dut):
    """Runs last: every required coverage bin must have been hit by the stimulus above."""
    COV.report(dut._log.info)
    if os.environ.get("NK_COV_STRICT", "1") == "1":
        assert not COV.missing(), f"uncovered bins: {COV.missing()}"
