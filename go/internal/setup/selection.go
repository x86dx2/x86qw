package setup

// Selected lists the components an installation of options places, in
// catalog order: the chosen content for a player, the server components
// for a host, and everything they require. It is exactly what the
// installer installs, so sizes and lanes shown before match what lands.
func Selected(catalog *Catalog, options Options) []string {
	chosen := map[string]bool{}
	if options.Intent.WantsClient() {
		ids := catalog.Profiles[options.Content]
		if options.Content == "custom" {
			ids = options.Components
		}
		for _, id := range ids {
			chosen[id] = true
		}
	}
	kinds := map[string]string{}
	requires := map[string][]string{}
	for _, component := range catalog.Components {
		kinds[component.ID], requires[component.ID] = component.Kind, component.Requires
	}
	if options.Intent.WantsServer() {
		chosen["mvdsv"], chosen["ktx"] = true, true
		if options.Server.QTVPort != 0 {
			chosen["qtv"] = true
		}
		if options.Server.QWFWDPort != 0 {
			chosen["qwfwd"] = true
		}
	} else {
		// A profile may list the server programs; a player does not get them.
		for id := range chosen {
			if kinds[id] == "server" {
				delete(chosen, id)
			}
		}
	}
	for grew := true; grew; {
		grew = false
		for id := range chosen {
			for _, dependency := range requires[id] {
				if !chosen[dependency] {
					chosen[dependency], grew = true, true
				}
			}
		}
	}
	var ids []string
	for _, component := range catalog.Components {
		if chosen[component.ID] {
			ids = append(ids, component.ID)
		}
	}
	return ids
}
