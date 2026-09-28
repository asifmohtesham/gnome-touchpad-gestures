// Parses a module without running it, so imports that only exist inside the
// shell do not matter. Exit status 1 and the error if it does not parse.
import GLib from 'gi://GLib';
import System from 'system';
const [, bytes] = GLib.file_get_contents(ARGV[0]);
const source = new TextDecoder().decode(bytes);
try {
    Reflect.parse(source, {target: 'module'});
    print('parses');
} catch (error) {
    print(`${error.name}: ${error.message} (line ${error.lineNumber})`);
    System.exit(1);
}
