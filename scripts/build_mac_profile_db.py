
# build_mac_profile_db.py
# Extracts MAC → config profile mappings from legacy switches

import csv
import json
import re
from netmiko import ConnectHandler
from pathlib import Path

INVENTORY_FILE = Path("../data/switch_inventory.csv")
OUTPUT_FILE = Path("../data/mac_profiles.json")

def load_inventory():
    with open(INVENTORY_FILE, newline='') as f:
        return list(csv.DictReader(f))

def get_mac_to_interface(output):
    mac_to_if = {}
    for line in output.splitlines():
        parts = line.split()
        # Cisco MAC table: VLAN  MAC Address    Type    Ports
        if len(parts) >= 3 and re.match(r"[0-9a-f]{4}\.[0-9a-f]{4}\.[0-9a-f]{4}", parts[1].lower()):
            mac = parts[1].replace('.', '').lower()
            mac = ":".join(mac[i:i+2] for i in range(0, len(mac), 2))
            mac_to_if[mac] = parts[-1]
    return mac_to_if

def get_interface_blocks(show_run):
    blocks = {}
    current_int = None
    lines = show_run.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("interface"):
            current_int = line.strip().split()[1]
            blocks[current_int] = [line]
        elif current_int:
            if line.startswith("!"):
                current_int = None
            else:
                blocks[current_int].append(line)
    return blocks

def extract_config_profile(int_lines):
    config = {
        "description": "",
        "mode": "access",
        "access_vlan": None,
        "voice_vlan": None,
        "portfast": False,
        "poe": "auto",
        "shutdown": False,
    }
    for line in int_lines:
        line = line.strip()
        if line.startswith("description"):
            config["description"] = line.partition(" ")[2]
        elif "switchport mode access" in line:
            config["mode"] = "access"
        elif "switchport mode trunk" in line:
            config["mode"] = "trunk"
        elif "switchport access vlan" in line:
            try:
                config["access_vlan"] = int(line.split()[-1])
            except Exception:
                pass
        elif "switchport voice vlan" in line:
            try:
                config["voice_vlan"] = int(line.split()[-1])
            except Exception:
                pass
        elif "spanning-tree portfast" in line:
            config["portfast"] = True
        elif "power inline never" in line:
            config["poe"] = "never"
        elif "power inline auto" in line:
            config["poe"] = "auto"
        elif line.strip() == "shutdown":
            config["shutdown"] = True
    return config

def main():
    inventory = load_inventory()
    master_db = {}

    for device in inventory:
        hostname = device['hostname']
        print(f"Connecting to {hostname}...")

        conn = ConnectHandler(
            device_type="cisco_ios",
            host=device['ip'],
            username="svcnetbrain",
            password="zcdbs5LSC775104106",
        )

        mac_table = conn.send_command("show mac address-table")
        show_run = conn.send_command("show run")
        conn.disconnect()

        mac_to_if = get_mac_to_interface(mac_table)
        interface_blocks = get_interface_blocks(show_run)

        for mac, intf in mac_to_if.items():
            config_lines = interface_blocks.get(intf)
            if config_lines:
                profile = extract_config_profile(config_lines)
                profile.update({"switch": hostname, "interface": intf})
                master_db[mac] = profile

    with open(OUTPUT_FILE, 'w') as f:
        json.dump(master_db, f, indent=2)

    print(f"Saved MAC profile DB to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()

