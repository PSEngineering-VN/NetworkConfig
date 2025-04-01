#!/usr/bin/env python3
import argparse
import sys
import re
import os
import csv
from getpass import getpass
from netmiko import ConnectHandler
import paramiko

def collect_mac_table(device):
    """
    Connects to the switch and retrieves the MAC address table,
    VLAN names from "show vlan brief", and the running configuration.
    """
    try:
        connection = ConnectHandler(**device)
        mac_output = connection.send_command('show mac address-table')
        
        # Uncomment these if you need raw output for debugging:
        # with open('raw_mac_output.txt', 'w') as file:
        #     file.write(mac_output)
        # print("Raw MAC address table output:")
        # print(mac_output)
        
        if not mac_output.strip():
            print("No data was returned from 'show mac address-table'. Check if the switch is properly configured to display MAC addresses.")
            return [], {}, "", "UnknownSwitch"
        vlan_output = connection.send_command('show vlan brief')
        running_config_output = connection.send_command('show running-config')

        # Retrieve hostname for default output file name
        hostname = connection.send_command('show running-config | include hostname').split()[-1]
        connection.disconnect()

        mac_entries = []
        vlan_names = {}
        parsing_entries = False

        # Process VLAN names from VLAN brief output
        for line in vlan_output.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].isdigit():
                vlan_id = parts[0]
                vlan_name = parts[1]
                vlan_names[vlan_id] = vlan_name

        # Process MAC table output
        for line in mac_output.splitlines():
            line = line.strip()
            if line.startswith('Vlan') and 'Mac Address' in line:
                parsing_entries = True
                continue

            if parsing_entries and line and not line.startswith('----'):
                parts = line.split()
                if len(parts) == 4 and parts[0].isdigit():
                    vlan, mac, _, interface = parts
                    vlan_name = vlan_names.get(vlan, "")  # Get VLAN name if it exists
                    mac_entries.append({'vlan': vlan, 'mac': mac.lower(), 'interface': interface, 'vlan_name': vlan_name})

        if len(mac_entries) == 0:
            print("No MAC addresses found. Proceeding with VLAN and running config collection.")
        else:
            print(f"Collected {len(mac_entries)} MAC address entries.")
        return mac_entries, vlan_names, running_config_output, hostname

    except Exception as e:
        print(f"Failed to collect MAC address table: {e}")
        return [], {}, "", "UnknownSwitch"


def save_to_single_file(mac_entries, vlan_names, running_config, hostname, output_file):
    """
    Saves all collected data to a single output file with sections.
    """
    try:
        with open(output_file, 'w') as file:
            file.write("=== MAC_ADDRESS_TABLE ===\n")
            if len(mac_entries) == 0:
                file.write("No MAC addresses found on the switch.\n")
            else:
                for entry in mac_entries:
                    file.write(f"{entry['vlan']},{entry['mac']},{entry['interface']},{entry['vlan_name']}\n")
            file.write("\n=== VLAN_NAMES ===\n")
            for vlan_id, vlan_name in vlan_names.items():
                file.write(f"{vlan_id},{vlan_name}\n")
            file.write("\n=== RUNNING_CONFIG ===\n")
            file.write(running_config)

        print(f"Data saved to {output_file}")
    except Exception as e:
        print(f"Failed to save data: {e}")


if __name__ == "__main__":
    # Configure Paramiko logging (optional, can be removed if not needed)
    paramiko.common.logging.basicConfig(level=paramiko.common.WARNING)
    # Force Paramiko to use older KEX algorithms, ciphers, and MACs if needed
    paramiko.Transport._preferred_kex = (
        'diffie-hellman-group-exchange-sha1',
        'diffie-hellman-group14-sha1',
        'diffie-hellman-group1-sha1',
    )
    paramiko.Transport._preferred_ciphers = (
        'aes128-cbc', '3des-cbc', 'aes192-cbc', 'aes256-cbc',
        'aes128-ctr', 'aes192-ctr', 'aes256-ctr',
    )
    paramiko.Transport._preferred_macs = (
        'hmac-md5', 'hmac-sha1', 'hmac-sha2-256', 'hmac-sha2-512',
    )

    # Set up command-line arguments
    parser = argparse.ArgumentParser(
        description="Collect MAC address table, VLAN names, and running configuration from a Cisco switch."
    )
    parser.add_argument("--host", required=True, help="IP address or hostname of the switch")
    parser.add_argument("--username", required=True, help="SSH username for the switch")
    parser.add_argument("--password", help="SSH password for the switch (if omitted, will prompt)")
    parser.add_argument("--output", default="", help="Output filename (default: <hostname>_config_collector_output.csv)")
    args = parser.parse_args()

    device = {
        'device_type': 'cisco_ios',
        'host': args.host,
        'username': args.username,
        'password': args.password if args.password else getpass("Enter switch password: ")
    }

    # Collect MAC table, VLAN names, and running config
    mac_table, vlan_names, running_config, hostname = collect_mac_table(device)

    # Determine output file name.
    default_filename = f"{hostname}_config_collector_output.csv"
    output_file = args.output if args.output else input(f"Enter output filename (default: {default_filename}): ").strip() or default_filename

    # Save results if data was collected.
    if running_config:
        save_to_single_file(mac_table, vlan_names, running_config, hostname, output_file)
    else:
        print("Failed to collect data.")
