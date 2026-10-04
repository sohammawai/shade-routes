import osmnx as ox

# Centre point of Connaught Place, Delhi (latitude, longitude)
place = (28.6315, 77.2167)

# Download all walkable streets within 800 metres of that point
G = ox.graph_from_point(place, dist=800, network_type="walk")

print(f"Downloaded {len(G.nodes)} intersections and {len(G.edges)} street segments")

# Draw the street network
ox.plot_graph(G, node_size=5, edge_color="gray", bgcolor="white")