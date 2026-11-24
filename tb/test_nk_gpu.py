import os
import sys
import zlib
import struct
import random
import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "driver")))
from nk_gpu_asm import encode
from nk_gpu_golden import NkGpuGoldenModel, expand_rgb565_to_888


def save_png_rgb(filename: str, width: int, height: int, pixels: list[list[tuple[int, int, int]]]):
    """Pure-Python PNG writer (no Pillow dependency required)."""
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        for x in range(width):
            r, g, b = pixels[y][x]
            raw.extend([r & 0xFF, g & 0xFF, b & 0xFF])

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack("!I", len(data)) + tag + data + struct.pack("!I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack("!IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(bytes(raw), 9)) + chunk(b"IEND", b"")
    with open(filename, "wb") as f:
        f.write(png)


async def reset_dut(dut):
    dut.rst_n.value = 0
    dut.host_sel.value = 0
    dut.host_addr.value = 0
    dut.host_wdata.value = 0
    dut.host_we.value = 0
    await ClockCycles(dut.clk, 5)
    dut.rst_n.value = 1
    await ClockCycles(dut.clk, 2)


async def mmio_write(dut, sel: int, addr: int, data: int):
    dut.host_sel.value = sel
    dut.host_addr.value = addr
    dut.host_wdata.value = data & 0xFFFFFFFF
    dut.host_we.value = 1
    await RisingEdge(dut.clk)
    dut.host_we.value = 0


async def mmio_read(dut, sel: int, addr: int) -> int:
    dut.host_sel.value = sel
    dut.host_addr.value = addr
    dut.host_we.value = 0
    await RisingEdge(dut.clk)
    return int(dut.host_rdata.value)


async def launch_warp(dut, warp_id: int, timeout: int = 400):
    await mmio_write(dut, 0, 0, ((warp_id & 0xFFFF) << 16) | 0x1)
    for _ in range(timeout):
        st = await mmio_read(dut, 0, 0)
        if st & 0x2:  # gpu_done
            return
    raise AssertionError(f"Warp {warp_id} timed out")


@cocotb.test()
async def test_simt_vector_mac_kernel(dut):
    """Run a multi-warp SIMT kernel: C[i] = A[i] * B[i] + D[i] across 4 warps (16 threads)."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    A = [random.randint(1, 200) for _ in range(16)]
    B = [random.randint(1, 200) for _ in range(16)]
    D = [random.randint(0, 500) for _ in range(16)]
    for i in range(16):
        await mmio_write(dut, 2, i, A[i])
        await mmio_write(dut, 2, 32 + i, B[i])
        await mmio_write(dut, 2, 64 + i, D[i])

    prog = [
        encode("SHL",  rd="R3", rs1="WID", imm16=2),
        encode("ADD",  rd="R3", rs1="R3",  rs2="TID"),
        encode("LD",   rd="R4", rs1="R3",  imm16=0),
        encode("LD",   rd="R5", rs1="R3",  imm16=32),
        encode("MUL",  rd="R6", rs1="R4",  rs2="R5"),
        encode("LD",   rd="R7", rs1="R3",  imm16=64),
        encode("ADD",  rd="R6", rs1="R6",  rs2="R7"),
        encode("ST",   rs1="R3", rs2="R6", imm16=96),
        encode("HALT"),
    ]
    for pc, ins in enumerate(prog):
        await mmio_write(dut, 1, pc, ins)

    for wid in range(4):
        await launch_warp(dut, wid)

    for i in range(16):
        got = await mmio_read(dut, 2, 96 + i)
        exp = A[i] * B[i] + D[i]
        assert got == exp, f"Thread {i} mismatch: got {got}, exp {exp}"


@cocotb.test()
async def test_virtual_gpu_shader_render(dut):
    """Execute a 32x32 RGB pixel shader on the Verilog SIMT GPU and save nk_gpu_render.png."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    W, H = 32, 32
    fb = [[(0, 0, 0) for _ in range(W)] for _ in range(H)]

    async def px_capture():
        while True:
            await RisingEdge(dut.clk)
            if dut.px_valid.value:
                x = int(dut.px_x.value)
                y = int(dut.px_y.value)
                rgb = int(dut.px_rgb.value)
                if 0 <= x < W and 0 <= y < H:
                    fb[y][x] = ((rgb >> 16) & 0xFF, (rgb >> 8) & 0xFF, rgb & 0xFF)

    cocotb.start_soon(px_capture())

    for y in range(H):
        prog = [
            encode("SHL",   rd="R3", rs1="WID", imm16=2),
            encode("ADD",   rd="R3", rs1="R3",  rs2="TID"),
            encode("SHL",   rd="R5", rs1="R3",  imm16=11),
            encode("MOVI",  rd="R6", imm16=(y * 2) << 5),
            encode("OR",    rd="R5", rs1="R5",  rs2="R6"),
            encode("ADDI",  rd="R7", rs1="R3",  imm16=y),
            encode("SHR",   rd="R7", rs1="R7",  imm16=1),
            encode("MOVI",  rd="R4", imm16=31),
            encode("AND",   rd="R7", rs1="R7",  rs2="R4"),
            encode("OR",    rd="R5", rs1="R5",  rs2="R7"),
            encode("SETPX", rs1="R3", rs2="R5", imm16=y),
            encode("HALT"),
        ]
        for pc, ins in enumerate(prog):
            await mmio_write(dut, 1, pc, ins)
        for wid in range(W // 4):
            await launch_warp(dut, wid)

    out_png = os.path.join(os.path.dirname(__file__), "nk_gpu_render.png")
    save_png_rgb(out_png, W, H, fb)
    assert os.path.exists(out_png) and os.path.getsize(out_png) > 100
    assert fb[0][0] == (0, 0, 0)
    assert fb[31][31][0] == 248 and fb[31][31][1] == 248


@cocotb.test()
async def test_directed_isa_corner_cases(dut):
    """Directed verification of all 14 opcodes, 16-bit overflow, shifts 0/15, RO regs, and DMEM wrap."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    gm = NkGpuGoldenModel()
    px_log = []

    async def px_capture():
        while True:
            await RisingEdge(dut.clk)
            if dut.px_valid.value:
                px_log.append((int(dut.px_x.value), int(dut.px_y.value), int(dut.px_rgb.value)))

    cocotb.start_soon(px_capture())

    for addr in range(128):
        val = (addr * 0x0101) & 0xFFFF
        gm.dmem[addr] = val
        await mmio_write(dut, 2, addr, val)

    prog = [
        encode("NOP"),
        # Attempt writes to read-only R0, R1, R2 (must be ignored)
        encode("MOVI", rd="R0", imm16=0xDEAD),
        encode("MOVI", rd="R1", imm16=0xBEEF),
        encode("MOVI", rd="R2", imm16=0xCAFE),
        # Overflow ADD & underflow SUB
        encode("MOVI", rd="R3", imm16=0xFFFF),
        encode("MOVI", rd="R4", imm16=0x0002),
        encode("ADD",  rd="R5", rs1="R3", rs2="R4"),     # 0x0001
        encode("SUB",  rd="R6", rs1="R4", rs2="R3"),     # 0x0003
        # MUL truncation & ADDI wrap
        encode("MOVI", rd="R7", imm16=0x1234),
        encode("ADDI", rd="R7", rs1="R7", imm16=0xF000), # 0x0234
        encode("MUL",  rd="R3", rs1="R7", rs2="R4"),     # 0x0468
        # Shifts by 0 (including 0x0010 -> imm16[3:0]==0) and 15
        encode("SHL",  rd="R4", rs1="R3", imm16=0x0010), # unchanged 0x0468
        encode("SHL",  rd="R4", rs1="R4", imm16=15),     # 0x0000 (bit 0 was 0)
        encode("MOVI", rd="R4", imm16=0x8001),
        encode("SHR",  rd="R4", rs1="R4", imm16=15),     # 0x0001
        encode("SHL",  rd="R4", rs1="R4", imm16=15),     # 0x8000
        # Store R0/R1/R2 to DMEM[0..2 + lane] to prove RO regs intact
        encode("ST",   rs1="R1", rs2="R0", imm16=0),
        encode("ST",   rs1="R1", rs2="R1", imm16=4),
        encode("ST",   rs1="R1", rs2="R2", imm16=8),
        # Store ALU results
        encode("ST",   rs1="R1", rs2="R5", imm16=12),
        encode("ST",   rs1="R1", rs2="R6", imm16=16),
        encode("ST",   rs1="R1", rs2="R4", imm16=20),
        # DMEM 7-bit address wrap: (127 + 1) & 0x7F == 0
        encode("MOVI", rd="R3", imm16=127),
        encode("LD",   rd="R5", rs1="R3", imm16=1),
        encode("ST",   rs1="R1", rs2="R5", imm16=24),
        # SETPX RGB565 -> RGB888 check
        encode("MOVI", rd="R6", imm16=0xF81F),           # max R, 0 G, max B
        encode("SETPX", rs1="R1", rs2="R6", imm16=7),
        encode("HALT"),
    ]
    for pc, ins in enumerate(prog):
        gm.imem[pc] = ins
        await mmio_write(dut, 1, pc, ins)

    gm.run_warp(5)
    await launch_warp(dut, 5)

    for addr in range(128):
        got = await mmio_read(dut, 2, addr)
        assert got == gm.dmem[addr], f"DMEM[{addr}] mismatch: got {got:#06x}, exp {gm.dmem[addr]:#06x}"

    assert px_log == gm.pixels, f"Pixel mismatch: got {px_log}, exp {gm.pixels}"
    assert px_log[0] == (0, 7, expand_rgb565_to_888(0xF81F))


@cocotb.test()
async def test_constrained_random_simt_fuzz(dut):
    """Run 20 constrained-random multi-warp programs and verify against NkGpuGoldenModel."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    rng = random.Random(20261124)
    gm = NkGpuGoldenModel()
    rtl_pixels = []

    async def px_capture():
        while True:
            await RisingEdge(dut.clk)
            if dut.px_valid.value:
                rtl_pixels.append((int(dut.px_x.value), int(dut.px_y.value), int(dut.px_rgb.value)))

    cocotb.start_soon(px_capture())

    alu_ops = ["NOP", "MOVI", "ADD", "SUB", "MUL", "ADDI", "AND", "OR", "SHR", "SHL", "LD", "ST", "SETPX"]
    all_regs = [f"R{i}" for i in range(8)]
    corner_imms = [0, 1, 2, 15, 16, 31, 127, 128, 0x00FF, 0x7FFF, 0x8000, 0xFFFF]

    for trial in range(20):
        await reset_dut(dut)
        gm.reset()
        rtl_pixels.clear()

        for addr in range(128):
            val = rng.randint(0, 0xFFFF)
            gm.dmem[addr] = val
            await mmio_write(dut, 2, addr, val)

        prog_len = rng.randint(18, 28)
        prog = []
        for idx in range(prog_len):
            op = alu_ops[idx % len(alu_ops)] if trial == 0 and idx < len(alu_ops) else rng.choice(alu_ops)
            rd = rng.choice(all_regs)
            rs1 = rng.choice(all_regs)
            rs2 = rng.choice(all_regs)
            imm = rng.choice(corner_imms) if rng.random() < 0.5 else rng.randint(0, 0xFFFF)
            prog.append(encode(op, rd=rd, rs1=rs1, rs2=rs2, imm16=imm))
        prog.append(encode("HALT"))

        for pc, ins in enumerate(prog):
            gm.imem[pc] = ins
            await mmio_write(dut, 1, pc, ins)

        num_warps = rng.randint(2, 4)
        for wid in range(num_warps):
            gm.run_warp(wid)
            await launch_warp(dut, wid)

        for addr in range(128):
            got = await mmio_read(dut, 2, addr)
            assert got == gm.dmem[addr], f"Trial {trial} DMEM[{addr}] mismatch: got {got:#06x}, exp {gm.dmem[addr]:#06x}"
        assert rtl_pixels == gm.pixels, f"Trial {trial} pixel mismatch"

    cocotb.log.info(f"Fuzz coverage: {gm.cov.summary()}")
    assert len(gm.cov.opcodes_seen) == 14, f"Missing opcodes: {gm.cov.opcodes_seen}"
    assert len(gm.cov.rd_seen) == 8 and len(gm.cov.rs1_seen) == 8 and len(gm.cov.rs2_seen) == 8


@cocotb.test()
async def test_mmio_guard_while_busy(dut):
    """Verify host MMIO writes to IMEM/DMEM are ignored while gpu_busy=1."""
    cocotb.start_soon(Clock(dut.clk, 2, units="ns").start())
    await reset_dut(dut)

    await mmio_write(dut, 2, 10, 0x1234)
    prog = [encode("NOP")] * 12 + [encode("HALT")]
    for pc, ins in enumerate(prog):
        await mmio_write(dut, 1, pc, ins)

    # Start warp, then immediately try to clobber DMEM[10] while gpu_busy=1
    await mmio_write(dut, 0, 0, 0x0001)
    await mmio_write(dut, 2, 10, 0xDEAD)
    for _ in range(50):
        if (await mmio_read(dut, 0, 0)) & 0x2:
            break
    assert (await mmio_read(dut, 2, 10)) == 0x1234
