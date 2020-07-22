#!/bin/sh

# Name
nvram set lan_hostname=example
nvram set router_name=Example
nvram set wan_domain=example.com
nvram set wan_hostname=example

# LAN
nvram set lan_ipaddr=192.168.123.1

# Wireless
for wl in wl0 wl1 wl2
do
nvram set ${wl}_akm=psk2
nvram set ${wl}_antdiv=3
nvram set ${wl}_country=US
nvram set ${wl}_country_code=US
nvram set ${wl}_security_mode=wpa2_personal
nvram set ${wl}_ssid='Example'
nvram set ${wl}_wpa_psk='redacted'
done

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

# Wireless (5 GHz #2)
nvram set wl2_channel=52
nvram set wl2_chanspec=52/80
nvram set wl2_nctrlsb=lower

# Admin Access
nvram set crt_ver=1
nvram set http_lanport=8080
nvram set http_passwd='redacted'
nvram set web_css=red
nvram set web_mx=status,tools

# Port Forward
nvram set portforward="\
1<1<<32400<<192.168.123.234<Plex Media Server>\
0<1<<3389<<192.168.123.123<Remote Desktop>"

# UPnP
nvram set upnp_clean=0
nvram set upnp_enable=1
nvram set upnp_lan=1
nvram set upnp_lan1=0
nvram set upnp_lan2=0
nvram set upnp_lan3=0

# DHCP
nvram set dhcp_lease=23
nvram set dhcp_num=64
nvram set dhcp_start=128
nvram set dhcpd_endip=192.168.123.191
nvram set dhcpd_startip=192.168.123.128
nvram set dhcpd_static="\
18:B4:30:00:00:03<192.168.123.100<Nest-Hello<0>\
18:B4:30:00:00:04<192.168.123.101<Nest<0>\
18:B4:30:00:00:05<192.168.123.102<Protect-Living-Room<0>\
18:B4:30:00:00:06<192.168.123.103<Protect-Hallway<0>\
18:B4:30:00:00:0C<192.168.123.104<Protect-Bedroom<0>\
18:B4:30:00:00:05<192.168.123.105<Protect-Master-Bedroom<0>"

# Time
nvram set ntp_server='0.us.pool.ntp.org 1.us.pool.ntp.org 2.us.pool.ntp.org'
nvram set tm_sel=CST6CDT,M3.2.0/2,M11.1.0/2
nvram set tm_tz=CST6CDT,M3.2.0/2,M11.1.0/2

# Logging
nvram set log_mark=0

# TomatoAnon
nvram set tomatoanon_answer=1
nvram set tomatoanon_enable=1
nvram set tomatoanon_id=0123456789

# Save
nvram commit
