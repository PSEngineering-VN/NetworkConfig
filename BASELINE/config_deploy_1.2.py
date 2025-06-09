#!/usr/bin/env python3
import argparse
import sys
import re
from netmiko import ConnectHandler

"""This version includes the omit for rop and trunk"""

def get_existing_vlans(connection):
    """Retrieve a set of VLAN IDs currently configured on the switch."""
    vlan_output = connection.send_command("show vlan brief")
    existing_vlans = set()
    for line in vlan_output.splitlines():
        match = re.match(r"^\s*(\d+)\s+", line)
        if match:
            existing_vlans.add(match.group(1))
    return existing_vlans

def parse_config_collector_output(file_path):
    """
    Parse the OLD_config_collector_output.csv file which contains multiple sections.
    
    === MAC_ADDRESS_TABLE ===
    1100,0050.7966.6802,Gi0/1,DATA
    1200,0050.7966.6803,Gi0/2,VOICE
    1300,0050.7966.6804,Gi0/3,WIRELESS
    1400,0050.7966.6805,Gi1/0,DMZ

    === VLAN_NAMES ===
    1,default
    20,VLAN0020
    100,VLAN0100
    1000,VLAN1000
    1002,fddi-default
    1003,token-ring-default
    1004,fddinet-default
    1005,trnet-default
    1100,DATA
    1200,VOICE
    1300,WIRELESS
    1400,DMZ

    === RUNNING_CONFIG ===
    (irrelevant for deployment)

    Returns:
      (mac_table_entries, vlan_names)
    where mac_table_entries is a list of dictionaries with keys: vlan, mac, interface, role;
    and vlan_names is a dict mapping VLAN IDs to VLAN names.
    """
    mac_table_entries = []
    vlan_names = {}
    current_section = None
    with open(file_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if line.startswith("==="):
                if "MAC_ADDRESS_TABLE" in line:
                    current_section = "mac"
                elif "VLAN_NAMES" in line:
                    current_section = "vlan"
                elif "RUNNING_CONFIG" in line:
                    current_section = "running"
                else:
                    current_section = None
                continue

            if current_section == "mac":
                parts = line.split(',')
                if len(parts) < 3:
                    continue
                entry = {
                    'vlan': parts[0].strip(),
                    'mac': parts[1].strip().lower(),
                    'interface': parts[2].strip(),  # original interface from CSV (may not be used)
                    'role': parts[3].strip() if len(parts) >= 4 else ""
                }
                mac_table_entries.append(entry)
            elif current_section == "vlan":
                parts = line.split(',')
                if len(parts) >= 2:
                    vlan = parts[0].strip()
                    name = parts[1].strip()
                    vlan_names[vlan] = name
    return mac_table_entries, vlan_names

def parse_new_switch_mac_table(output):
    """
    Parse the new switch's MAC address table output.
    Expected format:
          Mac Address Table
    -------------------------------------------
    
    Vlan    Mac Address       Type        Ports
    ----    -----------       --------    -----
       1    0050.7966.6802    DYNAMIC     Gi0/2
       1    0050.7966.6803    DYNAMIC     Gi0/3
       1    0050.7966.6804    DYNAMIC     Gi1/0
       1    0050.7966.6805    DYNAMIC     Gi0/1
       
    Returns:
       A dict mapping mac (in lowercase) → interface.
    """
    mac_map = {}
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        if re.match(r"^\d", line):
            parts = line.split()
            if len(parts) >= 4:
                mac = parts[1].lower()
                interface = parts[3]
                mac_map[mac] = interface
    return mac_map

def get_interface_modes(connection):
    """
    Retrieves interface modes by issuing "show interfaces switchport".
    Returns a dict mapping interface names to their Operational Mode.
    Only interfaces that have "static access" are considered acceptable.
    """
    output = connection.send_command("show interfaces switchport")
    interface_modes = {}
    current_intf = None
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("Name:"):
            parts = line.split()
            if len(parts) >= 2:
                current_intf = parts[1]
                interface_modes[current_intf] = None
        elif current_intf and "Operational Mode:" in line:
            # e.g., "Operational Mode: static access"
            parts = line.split("Operational Mode:")
            if len(parts) > 1:
                mode = parts[1].strip().lower()
                interface_modes[current_intf] = mode
                current_intf = None
    return interface_modes

def build_config_commands(mac_table_entries, vlan_names, existing_vlans, new_switch_mac_map, interface_modes, exclude_vlans):
    """
    Build configuration commands:
      1. Create any missing VLANs (unless excluded) using the VLAN_NAMES mapping.
      2. For each CSV entry (unless excluded), look up the new switch's interface by MAC address,
         and if that interface is in "static access" mode, assign it to the desired VLAN.
    """
    commands = []
    vlans_to_create = set()
    
    # Determine which VLANs from the CSV need to be created.
    for entry in mac_table_entries:
        vlan = entry.get('vlan')
        if not vlan or vlan in exclude_vlans:
            continue
        if vlan not in existing_vlans:
            vlans_to_create.add(vlan)
    
    # Generate VLAN creation commands.
    for vlan in sorted(vlans_to_create, key=lambda x: int(x)):
        vlan_name = vlan_names.get(vlan, f"VLAN_{vlan}")
        commands.append(f"vlan {vlan}")
        commands.append(f" name {vlan_name}")
    
    # For each CSV entry, look up the actual interface from the new switch MAC table.
    processed_interfaces = set()
    for entry in mac_table_entries:
        vlan = entry.get('vlan')
        mac = entry.get('mac')
        if not vlan or not mac or vlan in exclude_vlans:
            continue
        new_interface = new_switch_mac_map.get(mac)
        if not new_interface:
            print(f"Warning: MAC {mac} not found on new switch. Skipping this entry.")
            continue
        # Check if this interface is an access port (static access).
        mode = interface_modes.get(new_interface)
        if mode != "static access":
            print(f"Skipping interface {new_interface} (mode: {mode}) - not a static access port.")
            continue
        if new_interface in processed_interfaces:
            continue
        commands.append(f"interface {new_interface}")
        commands.append(f" switchport access vlan {vlan}")
        processed_interfaces.add(new_interface)
    
    return commands

def main():
    parser = argparse.ArgumentParser(
        description="Deploy configuration to switch based on config_collector.py output.csv file"
    )
    parser.add_argument("--csv-file", required=True, help="Path to config_collector.py output.csv file")
    parser.add_argument("--switch-ip", required=True, help="IP address of the new switch")
    parser.add_argument("--username", required=True, help="SSH username")
    parser.add_argument("--password", required=True, help="SSH password")
    parser.add_argument("--device-type", default="cisco_ios", help="Device type for Netmiko (default: cisco_ios)")
    parser.add_argument("--exclude-vlans", default="", help="Comma-separated list of VLAN IDs to exclude from configuration")
    args = parser.parse_args()
    
    # Process exclude VLANs argument.
    exclude_vlans = set()
    if args.exclude_vlans:
        for vlan in args.exclude_vlans.split(','):
            vlan = vlan.strip()
            if vlan:
                exclude_vlans.add(vlan)
    if exclude_vlans:
        print("Excluding VLANs:", sorted(exclude_vlans, key=lambda x: int(x)))
    
    device = {
        'device_type': args.device_type,
        'ip': args.switch_ip,
        'username': args.username,
        'password': args.password,
    }
    
    try:
        connection = ConnectHandler(**device)
    except Exception as e:
        sys.exit(f"Error connecting to the switch: {e}")
    
    existing_vlans = get_existing_vlans(connection)
    print("Existing VLANs on switch:", sorted(existing_vlans, key=lambda x: int(x)))
    
    # Parse the collector output file.
    mac_table_entries, vlan_names = parse_config_collector_output(args.csv_file)
    print(f"Parsed {len(mac_table_entries)} MAC address entries from CSV file.")
    print(f"Parsed {len(vlan_names)} VLAN name entries from CSV file.")
    
    # Get and parse the new switch's MAC address table.
    mac_table_output = connection.send_command("show mac address")
    new_switch_mac_map = parse_new_switch_mac_table(mac_table_output)
    print(f"Parsed {len(new_switch_mac_map)} entries from new switch MAC table.")
    
    # Retrieve interface modes from the switch.
    interface_modes = get_interface_modes(connection)
    print("Retrieved interface modes for", len(interface_modes), "interfaces.")
    
    # Build configuration commands using the new mapping, interface modes, and excluding specified VLANs.
    config_commands = build_config_commands(mac_table_entries, vlan_names, existing_vlans, new_switch_mac_map, interface_modes, exclude_vlans)
    
    if not config_commands:
        print("No configuration changes are necessary.")
        connection.disconnect()
        sys.exit(0)
    
    # Append commands to exit config mode and save changes.
    config_commands.extend(["end", "write memory"])
    
    # Display proposed configuration.
    print("\nProposed configuration commands:")
    print("--------------------------------")
    for cmd in config_commands:
        print(cmd)
    print("--------------------------------")
    
    answer = input("Do you want to apply these commands? (yes/no): ").strip().lower()
    if answer not in ["yes", "y"]:
        print("Aborting configuration changes.")
        connection.disconnect()
        sys.exit(0)
    
    print("\nApplying configuration...")
    output = connection.send_config_set(config_commands)
    print("Configuration applied. Device response:")
    print(output)
    
    connection.disconnect()

if __name__ == '__main__':
    main()
