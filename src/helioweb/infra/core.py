from terminusdb_client import WOQLClient


def get_terminusdb_client():
    team = "helioweb"
    client = WOQLClient("https://cloud.terminusdb.com/helioweb/")
    # make sure you have put the token in environment variable TERMINUSDB_ACCESS_TOKEN
    client.connect(team=team, use_token=True, db="main")
    return client
