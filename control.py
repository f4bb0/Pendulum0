import socket
import sys
import struct

# 确保控制台输出 UTF-8 编码，避免中文乱码
if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8')

# ================= 配置区 =================
# 推荐使用 255.255.255.255 进行局域网广播。
# 如果有多网卡导致广播失败，可换成具体的子网广播地址（如 192.168.1.255）或目标设备的具体 IP（如 192.168.1.100）
TARGET_IP = "255.255.255.255"  
TARGET_PORT = 4210            
# ==========================================

# ================= 协议常量定义 =================
CMD_SET_BASIC   = 0x01  # 设置基础参数 (角度, 显示)
CMD_SWING       = 0x02  # 设置摆动参数 (速度, 显示)
CMD_SWING_LIMIT = 0x03  # 设置摆动限位 (最小角度, 最大角度)
DEFAULT_MAGIC   = 0xAA

def build_udp_packet(cmd, start_id, devices_data, magic=DEFAULT_MAGIC):
    """
    构建符合 ESP32 协议的 UDP 数据包
    
    :param cmd: 命令码 (0x01, 0x02, 0x03)
    :param start_id: 起始设备 ID (0-255)
    :param devices_data: 设备数据列表，格式为 [(v1, v2), (v1, v2), ...]
                         支持 1-120 个设备。
                         如果不想改变某个值，请传入 None，会自动转为 0xFFFF (65535)
    :param magic: 魔术字节，默认 0xAA
    :return: 构建好的 bytes 数据包
    """
    if not (1 <= len(devices_data) <= 120):
        raise ValueError("设备数量必须在 1 到 120 之间")
        
    payload = bytearray()
    for v1, v2 in devices_data:
        # 处理 None 值，表示不改变当前值 (协议规定 0xFFFF 为不改变)
        val1 = 0xFFFF if v1 is None else int(v1)
        val2 = 0xFFFF if v2 is None else int(v2)
        
        # 范围保护
        if not (0 <= val1 <= 65535) or not (0 <= val2 <= 65535):
            raise ValueError(f"参数超出 uint16 范围: v1={val1}, v2={val2}")
            
        # 大端序 (Big-Endian) 打包为两个 uint16 (各 2 字节)
        payload.extend(struct.pack('>HH', val1, val2))
        
    payload_len = len(payload)
    
    # 构建包头: Magic(1) + startId(1) + cmd(1) + payloadLen(1)
    header = struct.pack('BBBB', magic, start_id, cmd, payload_len)
    
    # 计算校验和: 前面所有字节之和 mod 256 (与 C++ 中的 uint8_t 累加等效)
    checksum = (sum(header) + sum(payload)) & 0xFF
    
    # 拼接完整数据包
    packet = header + payload + bytes([checksum])
    return packet

def send_control_packet(sock, target_ip, target_port, cmd, start_id, devices_data, magic=DEFAULT_MAGIC):
    """
    构建并发送 UDP 控制数据包
    """
    packet = build_udp_packet(cmd, start_id, devices_data, magic)
    sock.sendto(packet, (target_ip, target_port))
    return packet

def send_raw_hex_packet(sock, target_ip, target_port, hex_string):
    """
    发送原始十六进制字符串数据包（用于快速测试或兼容旧协议）
    """
    data_to_send = bytes.fromhex(hex_string)
    sock.sendto(data_to_send, (target_ip, target_port))
    return data_to_send


# ================= 测试与使用示例 =================
if __name__ == "__main__":
    try:
        # 创建 UDP Socket (使用 with 语句确保执行完毕后自动关闭)
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp_socket:
            
            # 【核心步骤】开启 Socket 的广播权限 
            # (注：即使后续将 TARGET_IP 改为单播 IP，开启此选项通常也无副作用，且能保证广播模式正常工作)
            udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            
            print(f"开始向 {TARGET_IP}:{TARGET_PORT} 发送控制指令...\n")

            # ---------------------------------------------------------
            # 示例 0: 发送原始 HEX 字符串
            # 验证解析: AA(魔术) 01(ID) 01(命令) 08(长度8) 005A(90) 0063(99) 0000(0) 0000(0) 71(校验和)
            # ---------------------------------------------------------
            # raw_hex = "AA 01 01 08 00 5A 00 63 00 00 00 00 71"
            # pkt0 = send_raw_hex_packet(udp_socket, TARGET_IP, TARGET_PORT, raw_hex)
            # print(f"[示例0] 原始 HEX 广播发送成功！")
            # print(f"发送内容: {pkt0.hex(' ').upper()}")
            # print("-" * 60)

            # ---------------------------------------------------------
            # 示例 1: 控制单个设备 (ID=0)
            # 命令: CMD_SET_BASIC
            # 动作: 设置舵机角度为 90°，数码管显示 123
            # ---------------------------------------------------------
            # data1 = [(90, 123)]  
            # pkt1 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, 0, data1)
            # print(f"[示例1] 单设备基础设置 -> 发送 HEX: {pkt1.hex(' ').upper()}")
            
            # ---------------------------------------------------------
            # 示例 2: 控制多个设备 (ID=0 和 ID=1)
            # 命令: CMD_SWING
            # 动作: 设备0速度设为500(显示不变)，设备1速度设为800(显示456)
            # 技巧: 使用 None 表示不改变该参数 (底层自动转为 0xFFFF)
            # # ---------------------------------------------------------
            # data2 = [
            #     (90, 456),  # 设备 1: v1=500, v2=不改变
            #     (90, 456) ,   # 设备 2: v1=800, v2=456
            #     (90, 456)  ,  # 设备 3: v1=800, v2=456
            #     (90, 456)   , # 设备 4: v1=800, v2=456
            #     (90, 456)   , # 设备 5: v1=800, v2=456
            #     (90, 456)   , # 设备 6: v1=800, v2=456
            #     (90, 456)   , # 设备 7: v1=800, v2=456
            #     (90, 456)   , # 设备 8: v1=800, v2=456
            #     (90, 456)   , # 设备 9: v1=800, v2=456
            #     (90, 456)   ,# 设备 10: v1=800, v2=456
            #     (90, 456)   ,# 设备 11: v1=800, v2=456
            #     (90, 456)   # 设备 12: v1=800, v2=456
            # ]
            # pkt2 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, 1, data2)
            # print(f"[示例2] 多设备混合控制 -> 发送 HEX: {pkt2.hex(' ').upper()}")

            # ---------------------------------------------------------
            # 示例 3: 批量控制 10 个设备 (ID=10 到 19)
            # 命令: CMD_SWING_LIMIT
            # 动作: 将所有设备的摆动限位统一设置为 30° 到 90°
            # ---------------------------------------------------------
            data3 = [(30, 90) for _ in range(10)] 
            pkt3 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SWING_LIMIT, 10, data3)
            print(f"[示例3] 批量限位设置 (10个设备) -> 发送 HEX: {pkt3.hex(' ').upper()}")
            
            # ---------------------------------------------------------
            # 示例 4: 设备 1-12 全部摇摆
            # 命令: CMD_SWING
            # 动作: 将设备 1 到 12 的摆动速度统一设置为 500，数码管显示保持不变
            # ---------------------------------------------------------
            data4 = [(0, None) for _ in range(12)]
            pkt4 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SWING, 1, data4)
            print(f"[示例4] 1-12 设备全部摇摆 -> 发送 HEX: {pkt4.hex(' ').upper()}")
            
            # ---------------------------------------------------------
            # 示例 5: 设备 1-16 全部 30° 不动，显示 888
            # 命令: CMD_SET_BASIC
            # 动作: 将设备 1 到 16 的角度统一设置为 30，数码管显示设置为 888（显示值 888）
            # 技巧: 角度使用 30（0-65535 范围内），显示为 3 位数 888
            # ---------------------------------------------------------
            data5 = [(30, 888) for _ in range(16)]
            pkt5 = send_control_packet(udp_socket, TARGET_IP, TARGET_PORT, CMD_SET_BASIC, 1, data5)
            print(f"[示例5] 1-16 设备全部设为 30° 且显示 888 -> 发送 HEX: {pkt5.hex(' ').upper()}")
            
            
            print("\n✅ 所有指令发送完毕，Socket 已自动安全关闭。")
            
    except PermissionError:
        print("❌ 权限被拒绝：请检查防火墙设置，或尝试以管理员/Root权限运行此脚本。")
    except Exception as e:
        print(f"❌ 发送失败: {e}")