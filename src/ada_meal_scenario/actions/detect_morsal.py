import numpy, os, rospy, time, json
from bypassable_action import ActionException, BypassableAction
from std_msgs.msg import String
from catkin.find_in_workspaces import find_in_workspaces

import logging
logger = logging.getLogger('ada_meal_scenario')

#name which we will add indices to
morsal_base_name = 'morsal'
def morsal_index_to_name(ind):
    return morsal_base_name + str(ind)

class DetectMorsal(BypassableAction):

    def __init__(self, bypass=False):
        BypassableAction.__init__(self, 'DetectBite', bypass=bypass)


    def _run(self, robot, timeout=None):

        self.remove_morsals_next_indices(robot.GetEnv(), 0)
        
        m_detector = MorsalDetector(robot)
        m_detector.start()

        # Now wait for the morsal to be detected
        env = robot.GetEnv()
        logger.info('Waiting to detect morsal')
        start_time = time.time()
        time.sleep(1.0) # give time for the camera image to stabilize
        while not env.GetKinBody(morsal_index_to_name(0)) and (timeout is None or time.time() - start_time < timeout):
            print 'still waiting'
            time.sleep(1.0)

        #filter bad detections
        inds_to_filter = FilterMorsalsOnTable(env.GetKinBody('table'), m_detector.all_morsals)
        self.filter_morsal_inds(env, inds_to_filter, m_detector.all_morsals)
        ProjectMorsalsOnTable(env.GetKinBody('table'), m_detector.all_morsals)

        m_detector.stop()

        if not env.GetKinBody(morsal_index_to_name(0)):
            raise ActionException(self, 'Failed to detect any morsals.')

    def _bypass(self, robot, num_morsals=3):

        m_detector = MorsalDetector(robot)
        for i in range(num_morsals):
            # Here we want to place the kinbody
            #  somewhere in the environment
            morsal_in_camera = numpy.eye(4)
            #morsal_in_camera[:3,3] = [0.1, 0., 0.25]
            morsal_in_camera[:3,3] = [0.02, -0.02, 0.52]

            #add random noise
            rand_max_norm = 0.15
            morsal_in_camera[0:2, 3] += numpy.random.rand(2)*2.*rand_max_norm - rand_max_norm

            #switch to this if you want to test noise in world frame, not camera frame
            camera_in_world = robot.GetLink('Camera_Depth_Frame').GetTransform()
            morsal_in_world = numpy.dot(camera_in_world, morsal_in_camera)
#            morsal_in_world[0:2, 3] += numpy.random.rand(2)*2.*rand_max_norm - rand_max_norm
            #morsal_in_world[2,3] -= 0.17
            morsal_in_camera = numpy.dot(numpy.linalg.inv(camera_in_world), morsal_in_world)

            m_detector.add_morsal(morsal_in_camera, morsal_index_to_name(i))

        num_morsals_before_filter = len(m_detector.all_morsals)
        env = robot.GetEnv()
        
        ProjectMorsalsOnTable(env.GetKinBody('table'), m_detector.all_morsals)
        inds_to_filter = FilterMorsalsOnTable(env.GetKinBody('table'), m_detector.all_morsals)
        self.filter_morsal_inds(env, inds_to_filter, m_detector.all_morsals)

        #remove the kinbodies we used in previous timesteps not used here
        self.remove_morsals_next_indices(robot.GetEnv(), num_morsals, end_ind=num_morsals_before_filter)

    def filter_morsal_inds(self, env, inds_to_filter, all_morsals):
        """ Removes the OpenRAVE kin bodies for all morsals with index in inds_to_filter
        Also renames to ensure morsals in the environment have consecutive order

        @param env the OpenRAVE environment
        @param inds_to_filter indices to remove
        @param all_morsals list of all morsels currently in environment
        """
        #remove filtered morsals from env
        morsals_to_remove = [v for i,v in enumerate(all_morsals) if i in inds_to_filter]
        for morsal_to_remove in morsals_to_remove:
            env.Remove(morsal_to_remove)
        all_morsals = [v for i,v in enumerate(all_morsals) if i not in inds_to_filter]
        
        #rename to make sure consecutive order
        for ind,morsal in enumerate(all_morsals):
            morsal.SetName(morsal_index_to_name(ind))
        
    
    def remove_morsals_next_indices(self, env, start_ind, end_ind=0):
        """ Removes the OpenRAVE kin bodies for all morsals with index at
        or greater than the start index
        If end index is specified, will remove morsals up to that index
        Otherwise, will check indices until no morsals with index i is in the environment

        @param env the OpenRAVE environment
        @param start_ind the index to start checking
        @param end_ind the (optional) index to check morsals up until
        """

        ind = start_ind
        morsal_body = env.GetKinBody(morsal_index_to_name(ind))
        while morsal_body or ind < end_ind:
            if morsal_body:
                env.Remove(morsal_body)
            ind+=1
            morsal_body = env.GetKinBody(morsal_index_to_name(ind))


class MorsalDetector(object):
    
    def __init__(self, robot):
        self.env = robot.GetEnv()
        self.robot = robot
        self.sub = None
        self.all_morsals = []

    def start(self):
        logger.info('Subscribing to morsal detection')
        self.sub = rospy.Subscriber("/perception/morsel_detection", 
                                    String, 
                                    self._callback, 
                                    queue_size=1)
    
    def stop(self):
        logger.info('Unsubscribing from morsal detection')
        self.sub.unregister() # unsubscribe
        self.sub = None

    def add_morsal(self, morsal_in_camera, morsal_name=None):
        camera_in_world = self.robot.GetLink('Camera_Depth_Frame').GetTransform()
        morsal_in_world = numpy.dot(camera_in_world, morsal_in_camera)
        import openravepy
        h1 = openravepy.misc.DrawAxes(self.env, camera_in_world)
        h2 = openravepy.misc.DrawAxes(self.env, morsal_in_world)
        
        if morsal_name is None:
            morsal_name = 'morsal'
        
        object_base_path = find_in_workspaces(
            search_dirs=['share'],
            project='ada_meal_scenario',
            path='data',
            first_match_only=True)[0]
        ball_path = os.path.join(object_base_path, 'objects', 'smallsphere.kinbody.xml')
        if self.env.GetKinBody(morsal_name) is None:
            morsal = self.env.ReadKinBodyURI(ball_path)
            morsal.SetName(morsal_name)
            self.env.Add(morsal)
            morsal.Enable(False)
        else:
            morsal = self.env.GetKinBody(morsal_name)
        morsal.SetTransform(morsal_in_world)

        self.all_morsals.append(morsal)


        
    # TODO update this for multiple morsals
    def _callback(self, msg):
        logger.debug('Received detection')
        obj =  json.loads(msg.data)
        pts_arr = obj['pts3d']
        morsal_pos = numpy.asarray(pts_arr)
        if(morsal_pos is None) or(len(morsal_pos)==0):
            return

        for i in range(len(morsal_pos)):
          morsal_in_camera = numpy.eye(4)
          morsal_in_camera[:3,3] = morsal_pos[i]

          #check 
          self.add_morsal(morsal_in_camera, morsal_index_to_name(i))
        

def ProjectMorsalsOnTable(table, morsals, dist_above_table=0.01):
    """ Sets all morsals to be the specified distance above the table

    @param table the table kinbody
    @param morsals list of all morsals to project
    @param dist_above_table distance you want the bottom of the morsal to be above the table
    """
    all_morsal_dists = GetAllDistsTableToMorsal(table, morsals)
    for dist,morsal in zip(all_morsal_dists, morsals):
        morsal_transform = morsal.GetTransform()
        morsal_transform[2,3] -= dist
        morsal.SetTransform(morsal_transform)
    

def FilterMorsalsOnTable(table, morsals, thresh_dist_below_table=0.0, thresh_dist_above_table=0.1):
    """ Detects all morsals either below the table by more then the specified amount, or above
    by more then the specified amount, and returns their indices

    @param table the table kinbody
    @param morsals list of all morsals to project
    @param thresh_dist_below_table threshhold distance where we want to filter if the bottom of the morsal
        is below the table by more then this amount
    @param thresh_dist_above_table threshhold distance where we want to filter if the bottom of the morsal
        is above the table by more then this amount

    @return indices of morsals either below the table more then threshhold, or above by more then threshhold
    """
    all_morsal_dists = GetAllDistsTableToMorsal(table, morsals)
    inds_to_filter = []
    for ind,dist in enumerate(all_morsal_dists):
        if dist < thresh_dist_below_table or dist > thresh_dist_above_table:
            inds_to_filter.append(ind)

    return inds_to_filter


def GetAllDistsTableToMorsal(table, morsals):
    """ Get the distance between the top of the table and the bottom of each morsal

    @param table the table kinbody
    @param morsals list of all morsals to project
    @return the distance between the bottom of each morsal and the top of the table
    """
    table_aabb = table.ComputeAABB()
    top_of_table = table_aabb.pos()[2] + table_aabb.extents()[2]
    dists = []
    for morsal in morsals:
        morsal_aabb = morsal.ComputeAABB()
        bottom_of_morsal = morsal_aabb.pos()[2] - morsal_aabb.extents()[2]
        dist_diff = bottom_of_morsal - top_of_table
        dists.append(dist_diff)
    return dists

