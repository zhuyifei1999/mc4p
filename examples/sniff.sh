tac ~/projecto/research_projects/mcscraper/in/master_ip_list.txt > in_servers.txt
python frey_bulk_info.py in_servers.txt log >> out_servers.json