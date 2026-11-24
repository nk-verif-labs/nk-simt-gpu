create_clock -name clk -period 1600 [get_ports clk]
set_input_delay  250 -clock clk [get_ports {rst_n host_sel* host_addr* host_wdata* host_we}]
set_output_delay 250 -clock clk [all_outputs]
