`timescale 1ns / 1ps
`include "nk_gpu_isa.vh"

module nk_gpu_top (
    input  wire        clk,
    input  wire        rst_n,

    input  wire [1:0]  host_sel,
    input  wire [7:0]  host_addr,
    input  wire [31:0] host_wdata,
    input  wire        host_we,
    output reg  [31:0] host_rdata,

    output reg         px_valid,
    output reg  [7:0]  px_x,
    output reg  [7:0]  px_y,
    output reg  [23:0] px_rgb
);
    reg [31:0] imem [0:31];
    reg [15:0] dmem [0:127];

    reg        gpu_busy;
    reg        gpu_done;
    reg [5:0]  pc;
    reg [15:0] warp_id;

    // SIMT predication & 4-deep divergence stack
    reg [3:0]  active_mask;
    reg [3:0]  mask_stack [0:3];
    reg [2:0]  sp;

    reg        px_ser_busy;
    reg [1:0]  px_ser_idx;
    reg [3:0]  px_mask_lat;
    reg [7:0]  px_x_lat   [0:3];
    reg [15:0] px_rgb_lat [0:3];
    reg [7:0]  px_y_lat;

    wire [31:0] instr  = imem[pc[4:0]];
    wire [3:0]  opcode = instr[31:28];
    wire [2:0]  rd     = instr[26:24];
    wire [2:0]  rs1    = instr[22:20];
    wire [2:0]  rs2    = instr[18:16];
    wire [15:0] imm16  = instr[15:0];

    wire exec_en = gpu_busy && !px_ser_busy;

    wire [15:0] lane_a [0:3];
    wire [15:0] lane_b [0:3];
    wire [6:0]  lane_addr [0:3];
    wire [15:0] lane_rdata [0:3];
    wire [3:0]  eq_mask;
    wire [3:0]  lt_mask;

    genvar l;
    generate
        for (l = 0; l < 4; l = l + 1) begin : gen_lane
            assign lane_addr[l]  = lane_a[l][6:0] + imm16[6:0];
            assign lane_rdata[l] = dmem[lane_addr[l]];
            assign eq_mask[l]    = (lane_a[l] == lane_b[l]);
            assign lt_mask[l]    = (lane_a[l] <  lane_b[l]);

            nk_gpu_lane #(.LANE_ID(l)) u_lane (
                .clk(clk), .rst_n(rst_n), .warp_id(warp_id),
                .exec_en(exec_en && active_mask[l]), .opcode(opcode),
                .rd(rd), .rs1(rs1), .rs2(rs2), .imm16(imm16),
                .mem_rdata(lane_rdata[l]),
                .op_a_out(lane_a[l]), .op_b_out(lane_b[l])
            );
        end
    endgenerate

    always @(*) begin
        case (host_sel)
            2'b00:   host_rdata = {16'd0, 2'b00, pc, 6'd0, gpu_done, gpu_busy};
            2'b01:   host_rdata = imem[host_addr[4:0]];
            2'b10:   host_rdata = {16'd0, dmem[host_addr[6:0]]};
            default: host_rdata = 32'd0;
        endcase
    end

    wire [15:0] cur_565 = px_rgb_lat[px_ser_idx];

    reg branch_taken;
    always @(*) begin
        branch_taken = 1'b0;
        case (rd)
            3'd0: branch_taken = 1'b1;
            3'd1: branch_taken = |(active_mask & eq_mask);
            3'd2: branch_taken = |(active_mask & ~eq_mask);
            3'd3: branch_taken = |(active_mask & lt_mask);
            default: branch_taken = 1'b0;
        endcase
    end

    integer k;
    always @(posedge clk) begin
        if (!rst_n) begin
            gpu_busy <= 1'b0; gpu_done <= 1'b0; pc <= 6'd0; warp_id <= 16'd0;
            active_mask <= 4'b1111; sp <= 3'd0;
            for (k = 0; k < 4; k = k + 1) mask_stack[k] <= 4'b1111;
            px_ser_busy <= 1'b0; px_ser_idx <= 2'd0; px_mask_lat <= 4'b0000;
            px_valid <= 1'b0; px_x <= 8'd0; px_y <= 8'd0; px_rgb <= 24'd0; px_y_lat <= 8'd0;
            for (k = 0; k < 4; k = k + 1) begin
                px_x_lat[k] <= 8'd0; px_rgb_lat[k] <= 16'd0;
            end
        end else begin
            px_valid <= 1'b0;
            if (host_we && !gpu_busy) begin
                case (host_sel)
                    2'b00: begin
                        warp_id <= host_wdata[31:16];
                        if (host_wdata[0]) begin
                            gpu_busy    <= 1'b1;
                            gpu_done    <= 1'b0;
                            pc          <= 6'd0;
                            active_mask <= 4'b1111;
                            sp          <= 3'd0;
                        end
                    end
                    2'b01: imem[host_addr[4:0]] <= host_wdata;
                    2'b10: dmem[host_addr[6:0]] <= host_wdata[15:0];
                    default: ;
                endcase
            end

            if (px_ser_busy) begin
                px_valid <= px_mask_lat[px_ser_idx];
                px_x     <= px_x_lat[px_ser_idx];
                px_y     <= px_y_lat;
                px_rgb   <= {cur_565[15:11], 3'b000, cur_565[10:5], 2'b00, cur_565[4:0], 3'b000};
                if (px_ser_idx == 2'd3) begin
                    px_ser_busy <= 1'b0;
                    pc          <= pc + 6'd1;
                end else begin
                    px_ser_idx <= px_ser_idx + 2'd1;
                end
            end else if (gpu_busy) begin
                if (opcode == `OP_ST) begin
                    for (k = 0; k < 4; k = k + 1) begin
                        if (active_mask[k]) dmem[lane_addr[k]] <= lane_b[k];
                    end
                    pc <= pc + 6'd1;
                end else if (opcode == `OP_SETPX) begin
                    for (k = 0; k < 4; k = k + 1) begin
                        px_x_lat[k]   <= lane_a[k][7:0];
                        px_rgb_lat[k] <= lane_b[k];
                    end
                    px_y_lat    <= imm16[7:0];
                    px_mask_lat <= active_mask;
                    px_ser_busy <= 1'b1;
                    px_ser_idx  <= 2'd0;
                end else if (opcode == `OP_BR) begin
                    pc <= branch_taken ? {1'b0, imm16[4:0]} : (pc + 6'd1);
                end else if (opcode == `OP_SIMT) begin
                    case (rd)
                        3'd0: begin // IF_EQ
                            mask_stack[sp[1:0]] <= active_mask;
                            sp                  <= sp + 3'd1;
                            active_mask         <= active_mask & eq_mask;
                        end
                        3'd1: begin // IF_LT
                            mask_stack[sp[1:0]] <= active_mask;
                            sp                  <= sp + 3'd1;
                            active_mask         <= active_mask & lt_mask;
                        end
                        3'd2: begin // ELSE
                            active_mask <= mask_stack[sp[1:0] - 2'd1] & ~active_mask;
                        end
                        3'd3: begin // ENDIF
                            active_mask <= mask_stack[sp[1:0] - 2'd1];
                            sp          <= sp - 3'd1;
                        end
                        default: ;
                    endcase
                    pc <= pc + 6'd1;
                end else if (opcode == `OP_HALT) begin
                    gpu_busy <= 1'b0;
                    gpu_done <= 1'b1;
                end else begin
                    pc <= pc + 6'd1;
                end
            end
        end
    end
endmodule
