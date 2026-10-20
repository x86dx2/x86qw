package cli

// SetHandlers makes this system open qw:// links (links) and .mvd/.qwd
// demos (demos) with `x86qw open` of the installation at root, and stops
// it for what is off. Only handlers that belong to root are changed or
// removed; one that points elsewhere is left alone.
func SetHandlers(root string, links, demos bool) error { return setHandlers(root, links, demos) }

// HandlerBundleID names the macOS helper app that receives links and demos.
const HandlerBundleID = "br.com.x86.qw.links"

// DemoTypeID is the content type the helper app declares for demos.
const DemoTypeID = "br.com.x86.qw.demo"
