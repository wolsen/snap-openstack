package database

//go:generate -command mapper lxd-generate db mapper -t node.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node objects table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node objects-by-Member table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node objects-by-Name table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node objects-by-Role table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node objects-by-MachineID table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node id table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node create table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node delete-by-Name table=nodes
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e node update table=nodes
//
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node GetMany
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node GetOne
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node ID
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node Exists
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node Create
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node DeleteOne-by-Name
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e node Update

// Node is used to track Node information.
type Node struct {
	ID        int
	Member    string `db:"join=internal_cluster_members.name&joinon=nodes.member_id"`
	Name      string `db:"primary=yes"`
	Role      string
	MachineID int
}

// NodeFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type NodeFilter struct {
	Member    *string
	Name      *string
	Role      *string
	MachineID *int
}
