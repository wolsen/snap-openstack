// Package types provides shared types and structs.
package types

// JujuUsers is list of JujuUser struct
type JujuUsers []JujuUser

// JujuUser structure to hold juju user registration tokens
type JujuUser struct {
	Username string `json:"username" yaml:"username"`
	Token    string `json:"token" yaml:"token"`
}
