package main

import (
	"encoding/json"
	"fmt"
)

// catalogPins are the x86qw-cli binaries the catalog lists.
func catalogPins(raw []byte) (*pins, error) {
	var document struct {
		Packages []struct {
			Package, Version, Platform, Architecture, SHA256 string
			Size                                             int64
		} `json:"packages"`
	}
	if err := json.Unmarshal(raw, &document); err != nil {
		return nil, err
	}
	p := &pins{SHA256: map[string]string{}, Size: map[string]int64{}}
	for _, pkg := range document.Packages {
		if pkg.Package != "x86qw-cli" {
			continue
		}
		if p.Version != "" && pkg.Version != p.Version {
			return nil, fmt.Errorf("the catalog lists CLI %s and %s", p.Version, pkg.Version)
		}
		p.Version = pkg.Version
		for target, coordinates := range cliCoordinates {
			if coordinates == [2]string{pkg.Platform, pkg.Architecture} {
				p.SHA256[target], p.Size[target] = pkg.SHA256, pkg.Size
			}
		}
	}
	if len(p.SHA256) != len(Targets) {
		return nil, fmt.Errorf("the catalog does not list the %d CLI binaries", len(Targets))
	}
	return p, nil
}
