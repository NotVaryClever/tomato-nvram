# tomato-nvram

Find the tomato settings changed. Pretty-print the output.

Takes the current nvram dump, `nvram.txt`:

```
...
wl2_rateset=default
wl1_txpwr=0
wl1_nmcsidx=-1
tor_iface=br0
mysql_net_buffer_length=2
webmon_bkp=0
wl_macaddr=
wl1_bsd_if_select_policy=eth2 eth3
lan_route=
wl1_rx_amsdu_in_ampdu=off
wl0_mrate=0
wl1_channel=132
mysql_binary=internal
nginx_priority=10
wan3_modem_band=7FFFFFFFFFFFFFFF
wan3_proto=dhcp
qos_inuse=511
wan3_get_dns=
...
```

Compares it against an nvram dump of the defaults, `defaults.txt`:
```
...
lan_route=
wl1_bsd_if_select_policy=eth2 eth3
wl_macaddr=
webmon_bkp=0
mysql_net_buffer_length=2
tor_iface=br0
wl1_nmcsidx=-1
wl1_txpwr=0
wl2_rateset=default
wan3_proto=dhcp
wan3_modem_band=7FFFFFFFFFFFFFFF
nginx_priority=10
mysql_binary=internal
wl1_channel=100
wl0_mrate=0
wl1_rx_amsdu_in_ampdu=off
wan3_get_dns=
...
```

Generates a readable shell script from the difference, `set-nvram.sh`:
```
...

# LAN
nvram set lan_ipaddr=192.168.123.1

# Wireless (2.4 GHz)
nvram set wl0_bw_cap=1
nvram set wl0_channel=1
nvram set wl0_chanspec=1
nvram set wl0_nbw=20
nvram set wl0_nbw_cap=0
nvram set wl0_nctrlsb=lower

# Wireless (5 GHz)
nvram set wl1_channel=132
nvram set wl1_chanspec=132/80
nvram set wl1_radio=0
...
```

## Use

Requires: Python 3.x

**Save** the current settings as **`nvram.txt`**, from _Administration&rarr;Debugging&rarr;Download NVRAM Dump_ in the Tomato web UI, in the same directory as `tomato-nvram.py`.

**Reset** the router's NVRAM. Try to ensure that *all* the default settings have been set. This is how I do it:
* Erase all data in NVRAM. Wait for the router to boot.
* Reboot (because on my RT-AC66U, the 5 GHz radio doesn't show up otherwise).
* Click Save without changing anything on at least these sections:
  * _Basic&rarr;Network_
  * _Advanced&rarr;Wireless_
  * _Administration&rarr;Admin Access_

**Save** the defaults as **`defaults.txt`**, from _Administration&rarr;Debugging&rarr;Download NVRAM Dump_ in the Tomato web UI, in the same directory as `tomato-nvram.py`.

**Run** `tomato-nvram.py`:
```
$ ./tomato-nvram.py
102 settings written to set-nvram.sh
```