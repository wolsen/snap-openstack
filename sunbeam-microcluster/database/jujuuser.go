package database

//go:generate -command mapper lxd-generate db mapper -t jujuuser.mapper.go
//go:generate mapper reset
//
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e JujuUser objects table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e JujuUser objects-by-Username table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e JujuUser id table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e JujuUser create table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e JujuUser delete-by-Username table=jujuuser
//go:generate mapper stmt -d github.com/canonical/microcluster/cluster -e JujuUser update table=jujuuser
//
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser GetMany table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser GetOne table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser ID table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser Exists table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser Create table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser DeleteOne-by-Username table=jujuuser
//go:generate mapper method -i -d github.com/canonical/microcluster/cluster -e JujuUser Update table=jujuuser

// JujuUser is used to track User and registration token information.
type JujuUser struct {
	ID       int
	Username string `db:"primary=yes"`
	Token    string
}

// JujuUserFilter is a required struct for use with lxd-generate. It is used for filtering fields on database fetches.
type JujuUserFilter struct {
	Username *string
}
