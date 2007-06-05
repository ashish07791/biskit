##
## Biskit, a toolkit for the manipulation of macromolecular structures
## Copyright (C) 2004-2006 Raik Gruenberg & Johan Leckner
##
## This program is free software; you can redistribute it and/or
## modify it under the terms of the GNU General Public License as
## published by the Free Software Foundation; either version 2 of the
## License, or any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
## General Public License for more details.
##
## You find a copy of the GNU General Public License in the file
## license.txt along with this program; if not, write to the Free
## Software Foundation, Inc., 675 Mass Ave, Cambridge, MA 02139, USA.
##
##

## last $Author$
## last $Date$
## $Revision$

"""
Calculate Intervor interface description and project facet properties into
profiles of the input models.
"""

import tempfile, re
import numpy as N
import os.path as osp

import Biskit.Dock as D
import Biskit as B
import Biskit.tools as T
from Biskit import Executor, TemplateError


class IntervorError( Exception ):
    pass

class Intervor( Executor ):
    """
    """
    #: reg expressions for parsing
    RE_SECTION_EDGES = re.compile( '^Section Delaunay' )
    
    RE_facet = re.compile( '^(\d+) *\| *(\d+) *([\w\d]+) *(\w+) *(\w) *(\d+) *\| *(\d+) *([\w\d]+) *(\w+) *(\w) *(\d+) *\| *(\d+) +(\d+)')

    
    def __init__( self, model, cr=[0], cl=None, mode=2, breaks=0, **kw ):
        """
        Create a new Intervor instance for a given protein-protein complex.
        
        @param model: Structure of receptor, ligand and water
        @type  model: Biskit.PDBModel
        @param cr: receptor chains (default: [0] = first chain)
        @type  cr: [ int ]
        @param cl: ligand chains (default: None = all remaining protein chains)
        @type  cl: [ int ]
        @param breaks: consider chain breaks (backbone gaps) (default: 0)
        @type  breaks: bool or 1|0
        
        @param mode: what to calculate (default 2, = all with shelling order)
        @type  mode: int
        """
        Executor.__init__( self, 'intervor', **kw )
        
        assert isinstance( model, B.PDBModel ), \
               'requires PDBModel instance'
        assert model is not None, 'requires PDBModel instance'
        
        self.model = model
        self.breaks= breaks
        self.chains_rec = cr
        self.chains_lig = cl or self.__getLigandChains( model, cr )
        
        self.local_model = None   #: will hold modified copy of model
        
        self.mode = mode
        
        ## intervor puts several output files into current working directory
        ## but respect cwd from Executor.__init__ or ExeConfig/exe_intervor.dat
        self.cwd = self.cwd or tempfile.gettempdir()

        #: will be used by intervor for different output files
        self.f_prefix = tempfile.mktemp( dir=self.exe.cwd )
        self.f_pdb = self.f_prefix + '_intervor.pdb'
        
        self.result = {}
        
 
    def __getLigandChains( self, model, rec_chains ):
        """
        Determine which chains of a model belong to the ligand.
        @return: chain numbers of ligand
        @rtype : [ int ]
        """
        assert isinstance( model, B.PDBModel )

        chains = range( model.lenChains( breaks=self.breaks ) )
        
        ## mark every chain containing at least 1 protein residue with 1
        chain_mask_prot = model.atom2chainMask( model.maskProtein(),
                                            breaks=self.breaks )
        
        r = [ c for c in chains\
              if chain_mask_prot[c] and not c in rec_chains ]
        
        assert len(r) > 0, "Couldn't find any non-receptor protein chains"
        
        return r
    

    def __chainIds( self, model ):
        """
        @return: chain ids of receptor and ligand for intervor, eg. ('AB', 'C')
        @rtype : ( str, str )
        """
        ## get numpy array with chain_id of first atom of each chain
        chainids = N.take( model['chain_id'], 
                           model.chainIndex( breaks=self.breaks ) )
        
        ## extract receptor and ligand chain ids and concat them into string
        ids_rec = ''.join( N.take( chainids, self.chains_rec ) )
        ids_lig = ''.join( N.take( chainids, self.chains_lig ) )
        
        return ids_rec, ids_lig


    def __renameWaters( self, model ):
        """Intervor doesn't recognize TIP3 water entries -- rename to HOH"""

        i_h2o = N.flatnonzero( model.maskH2O() )  ## water atoms (indices)
        
        for i in i_h2o:
            model['residue_name'][i] = 'HOH'


    def prepare( self ):
        """
        Prepare PDB input file for Intervor. Called before program execution.
        * label chains consequtively from 'A' to ..
        * rename TIP3 or any other recognized water into 'HOH'
        * renumber all atoms consequtively starting with 0
        """
        m = self.model.clone()
        
        self.__renameWaters( m )

        m.addChainId()
        rec_ids, lig_ids = self.__chainIds( m )
        
        ## intervor reports serial number in facets --> make equal to index
        m['serial_number'] = range( len( m ) )

        ## override commandline args that will be used by run()
        self.args = "-f %s -o %i -C %s -C %s" %\
            (self.f_pdb, self.mode, rec_ids, lig_ids) 
        
        m.writePdb( self.f_pdb )


    def cleanup( self ):
        """
        Tidy up the mess we created. Called after program execution.
        """
        
        Executor.cleanup( self )
        
        if not self.debug:
            T.tryRemove( self.f_pdb )
    

    def isFailed( self ):
        """
        Overrides Executor method
        """
        return self.returncode != 1


    def __parseSO( self, fname ):
        """
        Parse intervor shelling order output file.
        
        @param fname: output file (*_intervor_SO.txt)
        @type  fname: str
        @return: raw table (list of dicts) with intervor output
        @rtype: [ {'edge_index':int, at1_serial:int, ... } ]
        """
        result = []

        int_keys = ['edge_index','at1_serial', 'at1_resindex',
                    'at2_serial', 'at2_resindex', 'so']
        
        try:
            l = f = None
            f = open( fname )
            
            ## handle file header (extract value keys from header line)
            l = f.readline()
            while not self.RE_SECTION_EDGES.match(l):
                    l = f.readline()
            
            l = f.readline().replace('|', ' ')
            keys = map( str.lower, l.split() )
            
            ## handle table (parse values into one dictionary for each line)
            while f:
                l = f.readline()
                if len(l) < 3:
                    break
                
                #l = l.replace( '|', ' ' )
                values = self.RE_facet.match( l ).groups()
                
                d = dict( zip( keys, values ) )
                
                for k in int_keys:
                    d[ k ] = int( d[ k ] )
                d['facet_area'] = float( d['facet_area'] )
                
                result += [ d ]
            
        except IOError, why:
            raise IntervorError, 'Error accessing intervor output file %r: %r'\
                  % (fname, why)
        except Exception, why:
            raise IntervorError, 'Error parsing intervor output file %r: %r'\
                  % (fname, why) + '\nThe offending line is: ' + repr(l)
        
        finally:
            try:
                f.close()
                N.arra
            except:
                pass
        
        return result

    def __mapfacets2Atoms( self, raw_table ):
        """
        Create an atom profile 'facets' that contains the facet
        dictionaries each atom is involved in ( empty list if the atom is not
        part of the interface).
        The entries of the profile are Biskit.DictList objects that function
        like normal lists but have additional functions to directly extract 
        values of the dictionaries in the list.
        """
        facet_profile = [ B.DictList() for i in self.model.atomRange() ]
        
        for facet in raw_table:
            a1 = facet['at1_serial']
            a2 = facet['at2_serial']
            
            #if facet_profile[a1] is None:
                #facet_profile[a1] = B.DictList()
            #if facet_profile[a2] is None:
                #facet_profile[a2] = B.DictList()

            facet_profile[a1] += [ facet ]
            facet_profile[a2] += [ facet ]
        
        self.model.atoms.set('facets', facet_profile, comment=\
                             'intervor facets this atom is involved in' )

        self.model['n_facets'] = [ len(f or []) for f in facet_profile ]
        self.model['n_facets','comment'] = 'number of interface facets'

    def __createProfiles( self, model ):
        """
        Create shelling order profiles from raw facet profile.
        """
        
        assert 'facets' in model.atoms, "profile 'facets' not found"
        
        so_min = [ min( f.valuesOf( 'so' ) or [0] ) for f in model['facets'] ]
        so_max = [ max( f.valuesOf( 'so' ) or [0] ) for f in model['facets'] ]

        facet_area = [ N.sum( f.valuesOf( 'facet_area') or [0])
                       for f in model['facets'] ]
        
        model['so_min'] = so_min
        model['so_max'] = so_max
        model['facet_area'] = facet_area
        
        model['so_min', 'comment'] = 'smallest Intervor shelling order of any'\
             + ' facet this atom is involved in'

        model['so_max', 'comment'] = 'highest Intervor shelling order of any'\
             + ' facet this atom is involved in'

        model['facet_area', 'comment'] = 'sum of area of all intervor facets' \
             + ' this atom is involved in'

    def finish( self ):
        """Called after a successful intervor run. Overrides Executor hook.
        """
        Executor.finish( self )
        f_so = self.f_prefix + '_intervor_SO.txt'
        
        raw = self.__parseSO( f_so )
        self.result = raw
        
        self.__mapfacets2Atoms( raw )
        self.__createProfiles( self.model )
    
        
    def visualize( self, profile='so_min' ):
        """
        @return: Pymoler instance with receptor and ligand colored by shelling
                 order (use pm.run() to run)
        @rtype: Pymoler
        """
        import Biskit.Pymoler as Pymoler
        model = self.model.clone()
        
        ## replace all 0 values by -1 to distinguish them better by color
        model[profile] += (model[profile] == 0) * -1 
        
        rec = model.takeChains( [0] )
        lig = model.takeChains( [1] )
        xwater = model.takeChains( [2] ) ## X-ray determined water
        water  = model.takeChains( [3] ) ## coming from X-Plor solvation
        
        ## kick out waters that do not belong to the interface
        xwater = xwater.compress( N.greater( xwater['n_facets'], 0) )
        water = water.compress( N.greater( water['n_facets'], 0) )
        
        pm = Pymoler()
        
        pm.addPdb( model, 'com' )
        pm.addPdb( rec, 'rec' )
        pm.addPdb( lig, 'lig' )
        pm.addPdb( xwater, 'x-water' )
        pm.addPdb( water, 'water' )
        
        pm.colorAtoms( 'rec', rec[profile])
        pm.colorAtoms( 'lig', lig[profile])
        pm.colorAtoms( 'x-water', xwater[profile] )
        pm.colorAtoms( 'water', water[profile] )
        
        pm.add( 'hide' )
        pm.add( 'show surface, rec' )
        pm.add( 'show mesh, lig' )
        pm.add( 'show spheres, x-water' )
        pm.add( 'show spheres, water' )
        pm.add( 'zoom' )
        
        pm.run()
        
######## TESTING #########

model = B.PDBModel( T.testRoot() + '/com/1BGS.pdb' )

x = Intervor( model, [0], debug=0, verbose=1 )
x.run()

pm = x.visualize( profile='facet_area' )



