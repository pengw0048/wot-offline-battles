#!/usr/bin/env python3
"""Keep mounted source packages immutable and publish runtime FXO caches atomically."""

import argparse
from pathlib import Path

MARKER = 'Standalone resource writes belong only to the first private overlay.'


def replace_once(source, before, after, label):
    if source.count(before) != 1:
        raise ValueError('Pinned resource write contract changed: ' + label)
    return source.replace(before, after, 1)


def function_span(source, signature):
    start = source.index(signature)
    opening = source.index('{', start)
    depth = 1
    end = opening + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return opening, end


def replace_body(source, signature, body):
    opening, end = function_span(source, signature)
    return source[:opening] + '{\n' + body + '\n}' + source[end:]


CACHE_SAVE = r'''
    // Publish a complete, standalone FXO into the private overlay. No source
    // archive or previously valid cache entry changes until the final rename.
    bool saveCompiledEffectCache( const BW::string& name, BinaryPtr data )
    {
        if (!data) return false;
        BWResource::ensurePathExists( name );
        MultiFileSystem* files = BWResource::instance().fileSystem();
        static LONG sequence = 0;
        char suffix[80];
        bw_snprintf( suffix, sizeof(suffix), ".tmp.%lu.%ld",
            GetCurrentProcessId(), InterlockedIncrement( &sequence ) );
        const BW::string temporary = name + suffix;
        FILE* output = files->posixFileOpen( temporary, "wb" );
        if (!output) return false;
        bool complete = data->len() == 0 ||
            fwrite( data->cdata(), 1, data->len(), output ) ==
                static_cast<size_t>(data->len());
        if (fflush( output ) != 0) complete = false;
        if (fclose( output ) != 0) complete = false;
        if (complete) complete = files->moveFileOrDirectory( temporary, name );
        if (!complete)
        {
            files->eraseFileOrDirectory( temporary );
            return false;
        }
        BWResource::instance().purge( name, true );
        return true;
    }
'''


def patch(engine):
    paths = {name: engine / relative for name, relative in (
        ('multi', 'lib/resmgr/multi_file_system.cpp'),
        ('zip', 'lib/resmgr/zip_file_system.cpp'),
        ('header', 'lib/resmgr/zip_file_system.hpp'),
        ('compiler', 'lib/moo/effect_compiler.cpp'))}
    contents = {name: path.read_text() for name, path in paths.items()}
    if MARKER in contents['multi']:
        if not all(token in contents[name] for name, token in (
                ('zip', 'mountedReadOnly_ = true;'),
                ('header', 'bool mountedReadOnly_ = false;'),
                ('compiler', 'saveCompiledEffectCache( fxoName,'))):
            raise ValueError('Incomplete resource overlay patch')
        return

    multi = contents['multi']
    multi = replace_once(multi, '#include "file_streamer.hpp"\n',
                         '#include "file_streamer.hpp"\n\n// ' + MARKER + '\n', 'overlay owner')
    for method, arguments, changed in (
            ('makeDirectory', 'path', 'path'),
            ('writeFile', 'path, pData, binary', 'BWResource::getFilePath( path )'),
            ('moveFileOrDirectory', 'oldPath, newPath', None),
            ('copyFileOrDirectory', 'oldPath, newPath', None),
            ('eraseFileOrDirectory', 'path', 'BWResource::getFilePath( path )')):
        body = '\tBW_GUARD;\n'
        if method == 'writeFile':
            body += '\tBWResource::ensurePathExists( path );\n'
        body += '\tReadWriteLock::ReadGuard bfsGuard( baseFileSystemsLock_ );\n'
        body += '\tif (baseFileSystems_.empty() ||\n'
        body += '\t\t!baseFileSystems_.front()->%s( %s )) return false;\n' % (method, arguments)
        if changed is None:
            body += '\tthis->addModifiedDirectory( BWResource::getFilePath( oldPath ) );\n'
            body += '\tthis->addModifiedDirectory( BWResource::getFilePath( newPath ) );\n'
        else:
            body += '\tthis->addModifiedDirectory( %s );\n' % changed
        body += '\treturn true;'
        multi = replace_body(multi, 'bool MultiFileSystem::' + method + '(', body)
    multi = replace_body(multi, 'bool MultiFileSystem::writeFileIfExists(', r'''
    // Preserve logical existence across read mounts, but save a private shadow.
    if (this->getFileType( path ) == FT_NOT_FOUND) return false;
    return this->writeFile( path, pData, binary );''')
    signature = 'FILE * MultiFileSystem::posixFileOpen('
    opening, unused = function_span(multi, signature)
    multi = multi[:opening + 1] + r'''
    // r+ can mutate a file too; never let a write failure try another mount.
    if (strpbrk( mode, "wa+" ))
    {
        ReadWriteLock::ReadGuard bfsGuard( baseFileSystemsLock_ );
        FILE* output = baseFileSystems_.empty() ? NULL :
            baseFileSystems_.front()->posixFileOpen( path, mode );
        if (output) this->addModifiedDirectory( BWResource::getFilePath( path ) );
        return output;
    }
''' + multi[opening + 1:]

    header = replace_once(contents['header'], '\tuint32\t\t\t\tsize_;',
        '\tuint32\t\t\t\tsize_;\n'
        '\tbool mountedReadOnly_ = false;\n'
        '\tbool mountedReadOnly() const\n'
        '\t{ return mountedReadOnly_ || (parentZip_ && parentZip_->mountedReadOnly()); }',
        'mounted archive immutability')
    zip_source = contents['zip']
    opening, unused = function_span(zip_source,
        'ZipFileSystem::ZipFileSystem( const BW::StringRef& zipFile ) :')
    zip_source = zip_source[:opening + 1] + '\n\tmountedReadOnly_ = true;\n' + zip_source[opening + 1:]
    for signature, failure in (
            ('bool ZipFileSystem::writeFile(', 'false'),
            ('bool ZipFileSystem::writeFileIfExists(', 'false'),
            ('bool ZipFileSystem::writeToStructure(', 'false'),
            ('bool ZipFileSystem::eraseFileOrDirectory(', 'false'),
            ('BinaryPtr ZipFileSystem::createZip(', 'NULL'),
            ('void ZipFileSystem::clear(const', '')):
        opening, unused = function_span(zip_source, signature)
        guard = ('\n\tif (this->mountedReadOnly())\n\t{\n'
                 '\t\tERROR_MSG( "ZipFileSystem: refusing to mutate mounted archive %%s\\n", path_.c_str() );\n'
                 '\t\treturn%s;\n\t}\n') % (' ' + failure if failure else '')
        zip_source = zip_source[:opening + 1] + guard + zip_source[opening + 1:]
    opening, unused = function_span(zip_source, 'FILE * ZipFileSystem::posixFileOpen(')
    zip_source = zip_source[:opening + 1] + r'''
    if (this->mountedReadOnly() && strpbrk( mode, "wa+" ))
    {
        ERROR_MSG( "ZipFileSystem: refusing to write mounted archive %s\n", path_.c_str() );
        return NULL;
    }
''' + zip_source[opening + 1:]

    compiler = replace_once(contents['compiler'], 'namespace {\n',
        'namespace {\n' + CACHE_SAVE, 'atomic compiled cache writer')
    start = compiler.index('\t\tBWResource::instance().fileSystem()->eraseFileOrDirectory(')
    end = compiler.index('\n\t\tIF_NOT_MF_ASSERT_DEV( pSection )', start)
    compiler = compiler[:start] + r'''
        // Build independently of any section owned by an original package.
        BW::vector<DataSectionPtr> emptyChildren;
        DataSectionPtr pSection = new ZipSection( fxoName,
            BWResource::instance().fileSystem(), &emptyChildren );
''' + compiler[end:]
    start = compiler.index('\t\t\t// Now actually save...')
    end = compiler.index('\n\t\t}\n\n\t\tif (warn)', start)
    compiler = compiler[:start] + '''\t\t\twarn = !saveCompiledEffectCache( fxoName, pSection->asBinary() );''' + compiler[end:]
    compiler = replace_once(compiler, '\n\t\tparentSection = NULL;', '', 'detached cache parent')
    for name, source in (('multi', multi), ('header', header),
                         ('zip', zip_source), ('compiler', compiler)):
        paths[name].write_text(source)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('engine', type=Path)
    patch(parser.parse_args().engine)
